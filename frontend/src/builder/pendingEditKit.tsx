// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The pieces a link rule, routing domain or boundary window edits its object
 *  with. The object is edited as a pending edit: a card sends its whole card
 *  value, and the field editor sends batches of field changes. The backend
 *  answers each with the object's new working JSON, the card value that writes
 *  it back (or none), the field editor over it and its issues. The page holds
 *  the latest answer as the object's pending edit and accepts nothing.
 */

import { useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import {
  GraphicalControlTreeEditor,
  type BuilderControlMutation,
} from "./GraphicalControlTreeEditor";
import type { BuilderIssue, BuilderVisualObjectEditResult } from "./generated/builderApi";

/** How long a typed change waits for the next keystroke before it is sent. */
const TYPING_PAUSE_MS = 350;

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/** Send one object's changes so that only the latest one's outcome counts.
 *  The function returned resolves true when its change was still the latest
 *  when it settled, and false when a later change was sent meanwhile. It
 *  rejects only when the latest change failed: each change states the whole
 *  object, so a later one replaces an earlier one's outcome. */
export function useLatestObjectChange<C>(
  send: (change: C) => Promise<unknown>,
): (change: C) => Promise<boolean> {
  const latest = useRef(0);
  return async (change: C) => {
    const request = ++latest.current;
    try {
      await send(change);
    } catch (cause) {
      if (request === latest.current) throw cause;
      return false;
    }
    return request === latest.current;
  };
}

export interface CardValueEdits<T> {
  /** The card value as shown: the value the card last asked for while it has
   *  no answer, otherwise the value the latest answer states. */
  readonly shown: T;
  /** Change the card value. A typed change waits for a pause in typing; any
   *  other change is sent at once, together with a typed change still
   *  waiting. */
  readonly change: (update: (value: T) => T, typed?: boolean) => void;
  /** Whether a change is waiting to be sent or waiting for its answer. */
  readonly checking: boolean;
  /** Why the latest change failed; null when it did not. */
  readonly error: string | null;
}

/** A card's edits of one object. Each change applies to the value the card
 *  last asked for, so changes made before an answer arrives build on each
 *  other. `send` resolves true when its value was the latest the object was
 *  sent. A change still waiting when the card closes is not sent. */
export function useCardValueEdits<T extends object>(
  answered: T,
  send: (value: T) => Promise<boolean>,
): CardValueEdits<T> {
  const askedRef = useRef<T | null>(null);
  const [asked, setAsked] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    };
  }, []);
  /** The answer settled the latest change; show what it states unless a typed
   *  change is still waiting. */
  const settle = () => {
    if (timer.current !== null) return;
    askedRef.current = null;
    setAsked(null);
  };
  const flush = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    const value = askedRef.current;
    if (value === null) return;
    void send(value).then(
      (latest) => {
        if (!latest || !mounted.current) return;
        setError(null);
        settle();
      },
      (cause: unknown) => {
        if (!mounted.current) return;
        setError(messageOf(cause));
        settle();
      },
    );
  };
  const change = (update: (value: T) => T, typed = false) => {
    const next = update(askedRef.current ?? answered);
    askedRef.current = next;
    setAsked(next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    if (typed) timer.current = window.setTimeout(flush, TYPING_PAUSE_MS);
    else flush();
  };
  return { shown: asked ?? answered, change, checking: asked !== null, error };
}

/** The field editor over an object's working JSON. It edits the tree the
 *  object's latest answer built at the accepted revision. When there is no such
 *  tree, because no edit of the object is pending or the session was accepted
 *  again since that answer, the backend describes the object first. */
export function PendingObjectFieldEditor({
  pending,
  appliedRevision,
  describe,
  send,
}: {
  /** The latest answer for the object's pending edit; null when none is pending. */
  pending: BuilderVisualObjectEditResult | null;
  appliedRevision: number;
  /** Ask the backend for the field editor over the object without changing it. */
  describe: () => Promise<BuilderVisualObjectEditResult>;
  /** Send one batch of field changes; resolves true when it was the latest. */
  send: (commands: ReadonlyArray<BuilderControlMutation>) => Promise<boolean>;
}) {
  const current =
    pending !== null && pending.control_tree.projection_revision === appliedRevision
      ? pending
      : null;
  // A description answers for the pending answer and the revision it was asked at.
  const [described, setDescribed] = useState<{
    pending: BuilderVisualObjectEditResult | null;
    revision: number;
    outcome: { answer: BuilderVisualObjectEditResult } | { failure: string };
  } | null>(null);
  const describedNow =
    described !== null && described.pending === pending && described.revision === appliedRevision
      ? described.outcome
      : null;
  const describeRef = useRef(describe);
  describeRef.current = describe;
  const needsDescription = current === null && describedNow === null;
  useEffect(() => {
    if (!needsDescription) return;
    let live = true;
    describeRef.current().then(
      (answer) => {
        if (live) setDescribed({ pending, revision: appliedRevision, outcome: { answer } });
      },
      (cause: unknown) => {
        if (live) {
          setDescribed({ pending, revision: appliedRevision, outcome: { failure: messageOf(cause) } });
        }
      },
    );
    return () => {
      live = false;
    };
  }, [needsDescription, pending, appliedRevision]);
  if (current === null && describedNow !== null && "failure" in describedNow) {
    return (
      <div className="builder-warning">
        The field editor could not be loaded: {describedNow.failure}{" "}
        <Button onClick={() => setDescribed(null)}>retry</Button>
      </div>
    );
  }
  const tree =
    current?.control_tree ??
    (describedNow !== null && "answer" in describedNow ? describedNow.answer.control_tree : null);
  if (tree === null) {
    return <div className="builder-site-derived">Loading the field editor…</div>;
  }
  return (
    <GraphicalControlTreeEditor
      tree={tree}
      onMutate={async (commands) => {
        await send(commands);
      }}
    />
  );
}

/** The backend's issues with an object's working JSON, each with the place in
 *  the object it names. */
export function PendingObjectIssues({ issues }: { issues: ReadonlyArray<BuilderIssue> }) {
  return (
    <>
      {issues.map((issue, index) => (
        <div
          className="builder-warning"
          key={`${issue.code}:${issue.json_pointer ?? ""}:${index}`}
        >
          {issue.message}
          {issue.json_pointer && (
            <span className="builder-site-derived"> {issue.json_pointer}</span>
          )}
        </div>
      ))}
    </>
  );
}
