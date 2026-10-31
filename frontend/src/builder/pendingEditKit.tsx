// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The pieces a link rule, routing domain or boundary window edits its object
 *  with. The object is edited as a pending edit: a card sends its whole card
 *  value, and the field editor sends batches of field changes. The backend
 *  answers each with the object's new working JSON, the card value that writes
 *  it back (or none), the field editor over it and its issues. The page holds
 *  the latest answer as the object's pending edit. Nothing is accepted until
 *  the window's Apply or OK sends the edit to the route that applies it.
 */

import { useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import {
  GraphicalControlTreeEditor,
  type BuilderControlMutation,
} from "./GraphicalControlTreeEditor";
import type {
  BuilderControlTree,
  BuilderIssue,
  BuilderPendingEditOutcome,
  BuilderVisualObjectControlEdit,
  BuilderVisualObjectEditResult,
} from "./generated/builderApi";

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
 *  sent. A change still waiting when the card closes is not sent. While a
 *  change is being typed or checked, the card holds a check of the object
 *  through `hold`, so the window's Apply and Remove wait for it. */
export function useCardValueEdits<T extends object>(
  answered: T,
  send: (value: T) => Promise<boolean>,
  hold: () => () => void,
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
  const checking = asked !== null;
  const holdRef = useRef(hold);
  holdRef.current = hold;
  useEffect(() => (checking ? holdRef.current() : undefined), [checking]);
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
  return { shown: asked ?? answered, change, checking, error };
}

/** The field editor's tree over an object's working JSON, while it is known. */
export type FieldEditorTree =
  | { readonly kind: "tree"; readonly tree: BuilderControlTree }
  | { readonly kind: "loading" }
  | { readonly kind: "failed"; readonly failure: string; readonly retry: () => void };

/** The tree the field editor over an object's working JSON edits: the tree the
 *  object's latest answer built at the accepted revision. When there is no such
 *  tree, because no edit of the object is pending or the session was accepted
 *  again since that answer, the backend describes the object first. Nothing is
 *  described while `needed` is false. */
export function useFieldEditorTree(
  /** The latest answer for the object's pending edit; null when none is pending. */
  pending: BuilderVisualObjectEditResult | null,
  appliedRevision: number,
  /** Ask the backend for the field editor over the object without changing it. */
  describe: () => Promise<BuilderVisualObjectEditResult>,
  needed: boolean,
): FieldEditorTree {
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
  const needsDescription = needed && current === null && describedNow === null;
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
  if (current !== null) return { kind: "tree", tree: current.control_tree };
  if (describedNow === null) return { kind: "loading" };
  if ("failure" in describedNow) {
    return { kind: "failed", failure: describedNow.failure, retry: () => setDescribed(null) };
  }
  return { kind: "tree", tree: describedNow.answer.control_tree };
}

/** What a field editor shows while its tree is not known. */
export function FieldEditorTreeWaiting({
  state,
}: {
  state: Exclude<FieldEditorTree, { readonly kind: "tree" }>;
}) {
  if (state.kind === "failed") {
    return (
      <div className="builder-warning">
        The field editor could not be loaded: {state.failure}{" "}
        <Button onClick={state.retry}>retry</Button>
      </div>
    );
  }
  return <div className="builder-site-derived">Loading the field editor…</div>;
}

/** The field editor over an object's working JSON, at the tree
 *  useFieldEditorTree finds for it. */
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
  const field = useFieldEditorTree(pending, appliedRevision, describe, true);
  if (field.kind !== "tree") return <FieldEditorTreeWaiting state={field} />;
  return (
    <GraphicalControlTreeEditor
      tree={field.tree}
      onMutate={async (commands) => {
        await send(commands);
      }}
    />
  );
}

/** The field editor over one object's pending edit with its issues, for an
 *  object no card edits. Only the latest change's outcome counts. */
export function PendingObjectFields({
  pending,
  appliedRevision,
  describe,
  onEdit,
}: {
  /** The latest answer for the object's pending edit; null when none is pending. */
  pending: BuilderVisualObjectEditResult | null;
  appliedRevision: number;
  /** Ask the backend for the field editor over the object without changing it. */
  describe: () => Promise<BuilderVisualObjectEditResult>;
  /** Send one field-editor batch; it settles once the answer is held. */
  onEdit: (change: BuilderVisualObjectControlEdit) => Promise<unknown>;
}) {
  const send = useLatestObjectChange(onEdit);
  return (
    <>
      <PendingObjectFieldEditor
        pending={pending}
        appliedRevision={appliedRevision}
        describe={describe}
        send={(commands) => send({ kind: "controls", commands })}
      />
      <PendingObjectIssues issues={pending?.issues ?? []} />
    </>
  );
}

/** The commit row of a link rule, routing domain or boundary list window.
 *  Apply and OK send the object's pending edit to the route that applies it.
 *  OK closes the window once the backend has answered, whether it applied the
 *  edit or refused it: a refused edit stays pending with its issues. A request
 *  that fails keeps the window open and shows the failure. Cancel discards the
 *  pending edit and closes the window, as the title-bar X does. A stale edit is
 *  never applied silently: the row offers to re-apply it over the object as the
 *  session now holds it, or to discard it. */
export function PendingEditApplyRow({
  pending,
  outcome,
  blocked,
  checking,
  onApply,
  onReapply,
  onDiscard,
  onClose,
}: {
  /** Whether an edit of the window's object is pending. */
  pending: boolean;
  /** The backend's latest outcome for the pending edit; null while it has
   *  stated none for the edit as it now stands. */
  outcome: BuilderPendingEditOutcome | null;
  /** Why the edit cannot be applied now, while Apply, OK and Re-apply wait;
   *  null when it can. */
  blocked: string | null;
  /** Whether a change of the object is still being typed or checked. Apply,
   *  OK and Re-apply wait for its answer, so they never apply an older edit
   *  or close the window over a change still on its way. */
  checking: boolean;
  /** Apply the pending edit; resolves whether the backend applied it. */
  onApply: () => Promise<boolean>;
  /** Re-apply a stale edit over the object as the session now holds it;
   *  resolves whether the backend applied it. */
  onReapply: () => Promise<boolean>;
  onDiscard: () => void;
  onClose: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const run = async (apply: () => Promise<boolean>, close: boolean) => {
    setRunning(true);
    setFailure(null);
    try {
      await apply();
    } catch (cause) {
      if (mounted.current) setFailure(messageOf(cause));
      return;
    } finally {
      if (mounted.current) setRunning(false);
    }
    if (close) onClose();
  };
  // The outcome the backend refused the pending edit with; none while no edit
  // is pending.
  const refused = pending && outcome !== null && outcome.status !== "applied" ? outcome : null;
  const stale = refused?.status === "stale" ? refused : null;
  const waiting = blocked !== null || running || checking;
  const waitingReason = running
    ? "The edit is being applied"
    : (blocked ?? (checking ? "The edit is being checked" : null));
  return (
    <>
      {stale && (
        <div className="builder-stale-notice" data-testid="builder-stale-notice">
          <span className="builder-stale-notice-text">
            The session changed since this edit began:{" "}
            {stale.issues.map((issue) => issue.message).join(" ")}
          </span>
          <Button
            onClick={() => void run(onReapply, false)}
            disabled={waiting}
            title={waitingReason ?? "Apply this edit over the object as the session now holds it"}
          >
            Re-apply
          </Button>
          <Button
            onClick={onDiscard}
            disabled={running || checking}
            title={
              checking
                ? "The edit is being checked"
                : "Drop this edit and show the object as the session holds it"
            }
          >
            Discard
          </Button>
        </div>
      )}
      {failure && <div className="builder-warning">{failure}</div>}
      <div className="builder-apply-row" data-testid="builder-apply-row">
        <span
          className={`builder-apply-state${
            refused ? " builder-apply-state--stale" : pending ? " builder-apply-state--dirty" : ""
          }`}
        >
          {!pending ? "applied" : refused ? refused.status : "pending edit"}
        </span>
        <Button
          onClick={() => {
            onDiscard();
            onClose();
          }}
          disabled={running}
          title="Discard the pending edit and close"
        >
          Cancel
        </Button>
        <Button
          onClick={() => void run(onApply, false)}
          disabled={!pending || waiting}
          title={waitingReason ?? "Apply the pending edit to the session"}
        >
          Apply
        </Button>
        <Button
          icon="check"
          onClick={() => (pending ? void run(onApply, true) : onClose())}
          disabled={waiting}
          title={waitingReason ?? "Apply and close"}
        >
          OK
        </Button>
      </div>
    </>
  );
}

/** Remove a link rule or routing domain from the session at once, as one
 *  accepted change, from its window whatever edits it: its card or the field
 *  editor. A removal starts from the object the session holds, so it waits
 *  while an edit of the object is pending or a change of it is being checked. */
export function RemoveAtOnceButton({
  object,
  onRemove,
  pending,
  checking,
}: {
  /** What the object is called in the button: "rule" or "domain". */
  object: string;
  /** Null while a change is being applied. */
  onRemove: (() => void) | null;
  /** Whether an edit of the object is pending. */
  pending: boolean;
  /** Whether a change of the object is being typed or checked. */
  checking: boolean;
}) {
  const reason =
    onRemove === null
      ? "Removing waits while a change is being applied."
      : pending
        ? `Removing waits while this ${object}'s edit is pending: apply or discard it first.`
        : checking
          ? `Removing waits while a change of this ${object} is being checked.`
          : null;
  return (
    <div className="builder-preset-row">
      <Button
        variant="danger"
        onClick={reason === null && onRemove !== null ? onRemove : undefined}
        disabled={reason !== null}
        title={reason ?? `Removes the ${object} from the session at once`}
      >
        Remove {object}
      </Button>
    </div>
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
