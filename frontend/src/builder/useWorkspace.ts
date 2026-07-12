// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace state: the applied session drafts plus bounded undo history.
 *
 *  The edit→compile loop lives in BuilderView, which submits the
 *  workspace with any open windows' working copies overlaid — the canvas
 *  previews what is being edited while the workspace itself only changes on
 *  Apply. The rendered world is always the resolver's expansion of that
 *  serialization — or the resolver's error, verbatim. No builder-local
 *  expansion, ever.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type DraftConstellation,
  type DraftGroundSet,
  type DraftBoundary,
  type DraftLinkRule,
  type DraftRoutingDomain,
  type Workspace,
} from "./workspace";

/** One editor window's working copy: `draft` is what the window edits,
 *  `opened` is the object as it stood before the first edit (the Defaults
 *  target), `dirty` means the draft has uncommitted changes. */
export interface EditorBuffer {
  draft: unknown;
  opened: unknown;
  dirty: boolean;
}

export type BufferMap = Record<string, EditorBuffer>;

// Buffer/stale equality is intentionally local interaction logic. Persisted
// document equality and canonicalization belong to VS-API.
function _bufferDeepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => _bufferDeepEqual(item, b[i]));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a as Record<string, unknown>);
    const kb = Object.keys(b as Record<string, unknown>);
    return (
      ka.length === kb.length &&
      ka.every((k) =>
        _bufferDeepEqual(
          (a as Record<string, unknown>)[k],
          (b as Record<string, unknown>)[k],
        ),
      )
    );
  }
  return false;
}

/** The workspace as the canvas previews it: the applied workspace with every
 *  dirty window's working copy substituted in. Pure — apply-all-and-save
 *  serializes exactly this and then commits it, so what was saved and what
 *  was adopted cannot diverge. Buffer keys are `kind` or `kind:id`. `skipKeys`
 *  omits specific buffers — the bulk apply-and-save confirm flow declines
 *  stale windows by leaving their keys out of the overlay, so a working copy
 *  the user did not confirm is never written. */
export function overlayBuffers(
  workspace: Workspace,
  buffers: BufferMap,
  skipKeys?: ReadonlySet<string>,
): Workspace {
  let out = workspace;
  for (const [key, buf] of Object.entries(buffers)) {
    if (!buf.dirty || skipKeys?.has(key)) continue;
    const sep = key.indexOf(":");
    const kind = sep === -1 ? key : key.slice(0, sep);
    const id = sep === -1 ? "" : key.slice(sep + 1);
    switch (kind) {
      case "session":
        // The session buffer is a field pick, never the whole workspace.
        out = { ...out, ...(buf.draft as Partial<Workspace>) };
        break;
      case "segment":
        out = {
          ...out,
          space: out.space.map((d) =>
            d.segment_id === id ? (buf.draft as DraftConstellation) : d,
          ),
        };
        break;
      case "ground":
        out = {
          ...out,
          ground: out.ground.map((d) =>
            d.segment_id === id ? (buf.draft as DraftGroundSet) : d,
          ),
        };
        break;
      case "link":
        out = {
          ...out,
          links: out.links.map((r) => (r.rule_id === id ? (buf.draft as DraftLinkRule) : r)),
        };
        break;
      case "domain":
        out = {
          ...out,
          routing_domains: out.routing_domains.map((d) =>
            d.domain_id === id ? (buf.draft as DraftRoutingDomain) : d,
          ),
        };
        break;
      case "boundary":
        out = {
          ...out,
          boundaries: out.boundaries.map((b) =>
            b.boundary_id === id ? (buf.draft as DraftBoundary) : b,
          ),
        };
        break;
    }
  }
  return out;
}

/** The applied object a buffer key names, or null when it no longer exists (or
 *  for the session kind, whose applied view is the field-pick the buffer holds,
 *  not a single object). Reconciliation, staleness, and "Load current values"
 *  all use this function. */
export function appliedObjectForKey(workspace: Workspace, key: string): unknown {
  const sep = key.indexOf(":");
  const kind = sep === -1 ? key : key.slice(0, sep);
  const id = sep === -1 ? "" : key.slice(sep + 1);
  switch (kind) {
    case "session": {
      // Compare the same field pick the buffer holds — its `opened` keys.
      return null;
    }
    case "segment":
      return workspace.space.find((d) => d.segment_id === id) ?? null;
    case "ground":
      return workspace.ground.find((d) => d.segment_id === id) ?? null;
    case "link":
      return workspace.links.find((r) => r.rule_id === id) ?? null;
    case "domain":
      return workspace.routing_domains.find((d) => d.domain_id === id) ?? null;
    case "boundary":
      return workspace.boundaries.find((b) => b.boundary_id === id) ?? null;
    default:
      return null;
  }
}

/** Whether a buffer's applied object has moved away from the `opened` base the
 *  buffer was taken against — an undo, restore, deletion, or sibling edit
 *  changed it (or, for the session kind, the field-pick moved). The one
 *  primitive under both staleness (a DIRTY such buffer is stale) and the
 *  clean-buffer drop (a CLEAN such buffer is re-seeded), so the two can never
 *  disagree about "changed underneath." */
export function bufferAppliedChanged(
  workspace: Workspace,
  key: string,
  buf: EditorBuffer,
): boolean {
  const kind = key.indexOf(":") === -1 ? key : key.slice(0, key.indexOf(":"));
  if (kind === "session") {
    const opened = buf.opened as Record<string, unknown> | null;
    if (!opened) return false;
    const pick = Object.fromEntries(
      Object.keys(opened).map((k) => [k, (workspace as unknown as Record<string, unknown>)[k]]),
    );
    return !_bufferDeepEqual(pick, opened);
  }
  const applied = appliedObjectForKey(workspace, key);
  return applied === null || !_bufferDeepEqual(applied, buf.opened);
}

/** Dirty buffers whose applied object still EXISTS but moved underneath them
 *  (undo, restore, a sibling edit) since the window's `opened` base was taken.
 *  Bulk apply-and-save refuses these — a bulk gesture must never silently apply
 *  a stale working copy over an object the user reverted. A buffer whose object
 *  was DELETED is deliberately NOT "stale": the reconciliation pass prunes its
 *  window and buffer outright, so there is no window left to carry a notice and
 *  nothing to apply into. Deleted objects are pruned separately from objects
 *  that changed while their editor remained open. */
export function staleBufferKeys(workspace: Workspace, buffers: BufferMap): string[] {
  const stale: string[] = [];
  for (const [key, buf] of Object.entries(buffers)) {
    if (!buf.dirty) continue;
    const kind = key.indexOf(":") === -1 ? key : key.slice(0, key.indexOf(":"));
    // The session pick has no single applied object (appliedObjectForKey is
    // null there by design); its staleness is the field-pick compare inside
    // bufferAppliedChanged. Only object-kind windows gate on existence.
    if (kind !== "session" && appliedObjectForKey(workspace, key) === null) continue;
    if (bufferAppliedChanged(workspace, key, buf)) stale.push(key);
  }
  return stale;
}

/** The exact workspace a save writes, under the dialog's stated gesture.
 *  applyAll overlays every dirty working copy — the session buffer
 *  included, so a Session-window rename survives. The dialog's name field
 *  wins only when the user actually edited it: an untouched field must
 *  never silently undo a rename the overlays carried in. */
export function workspaceForSave(
  workspace: Workspace,
  buffers: BufferMap,
  opts: {
    applyAll: boolean;
    dialogName: string;
    nameTouched: boolean;
    excludeKeys?: ReadonlySet<string>;
  },
): Workspace {
  const base = opts.applyAll
    ? overlayBuffers(workspace, buffers, opts.excludeKeys)
    : workspace;
  const sessionName = opts.nameTouched ? opts.dialogName : base.session_name;
  return base.session_name === sessionName ? base : { ...base, session_name: sessionName };
}
const HISTORY_LIMIT = 100;

export function useWorkspace() {
  const [workspace, setWorkspaceState] = useState<Workspace | null>(null);
  // The mutation path updates workspaceRef synchronously so a live-workspace
  // read never lags the commit for either the
  // value and updater forms — then set React state. Undo history observes the
  // state change; structured recovery is owned by structuredDraftRecovery.ts.
  const workspaceRef = useRef<Workspace | null>(null);
  const commit = useCallback(
    (update: Workspace | null | ((prev: Workspace | null) => Workspace | null)) => {
      const next =
        typeof update === "function"
          ? (update as (prev: Workspace | null) => Workspace | null)(workspaceRef.current)
          : update;
      workspaceRef.current = next;
      setWorkspaceState(next);
    },
    [],
  );

  // Every mutation lands in bounded undo history.
  const historyRef = useRef<(Workspace | null)[]>([]);
  const skipHistoryRef = useRef(false);
  const previousRef = useRef<Workspace | null>(null);
  useEffect(() => {
    if (skipHistoryRef.current) {
      skipHistoryRef.current = false;
    } else if (previousRef.current !== workspace) {
      historyRef.current.push(previousRef.current);
      if (historyRef.current.length > HISTORY_LIMIT) historyRef.current.shift();
    }
    previousRef.current = workspace;
  }, [workspace]);

  const undo = useCallback(() => {
    if (historyRef.current.length === 0) return;
    const past = historyRef.current.pop() as Workspace | null;
    skipHistoryRef.current = true;
    commit(past);
  }, []);

  /** Adopt a ready-made workspace returned by import or a backend operation. */
  const openWorkspace = useCallback((imported: Workspace) => {
    commit(imported);
  }, []);

  /** Atomic adoption of a next workspace the caller composed from the
   *  current one (apply-all-and-save, save-time rename). Rides the single
   *  mutation path: exactly one undo entry. Import
   *  adoption stays `openWorkspace`; apply-all must not abuse it. */
  const commitWorkspace = useCallback((next: Workspace, _reason: string) => {
    commit(next);
  }, []);

  /** Session-level plumbing: name, time, and the candidate budget. */
  const updateSession = useCallback(
    (
      patch: Partial<
        Pick<
          Workspace,
          | "session_name"
          | "start_time"
          | "step_seconds"
          | "compression"
          | "max_pairs_per_rule"
          | "max_pairs_per_tick"
        >
      >,
    ) => {
      commit((prev) => (prev ? { ...prev, ...patch } : prev));
    },
    [],
  );

  const close = useCallback(() => commit(null), []);
  const currentWorkspace = useCallback(() => workspaceRef.current, []);

  const removeRefSegment = useCallback((segmentId: string) => {
    commit((prev) =>
      prev
        ? { ...prev, space_refs: prev.space_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  const removeConstellation = useCallback((segmentId: string) => {
    commit((prev) =>
      prev
        ? { ...prev, space: prev.space.filter((d) => d.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  const updateConstellation = useCallback(
    (segmentId: string, patch: Partial<DraftConstellation>) => {
      commit((prev) =>
        prev
          ? {
              ...prev,
              space: prev.space.map((draft) =>
                draft.segment_id === segmentId ? { ...draft, ...patch } : draft,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeGroundRef = useCallback((segmentId: string) => {
    commit((prev) =>
      prev
        ? { ...prev, ground_refs: prev.ground_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  const updateLinkRule = useCallback((ruleId: string, patch: Partial<DraftLinkRule>) => {
    commit((prev) =>
      prev
        ? {
            ...prev,
            links: prev.links.map((rule) =>
              rule.rule_id === ruleId ? { ...rule, ...patch } : rule,
            ),
          }
        : prev,
    );
  }, []);

  const removeLinkRule = useCallback((ruleId: string) => {
    commit((prev) =>
      prev ? { ...prev, links: prev.links.filter((rule) => rule.rule_id !== ruleId) } : prev,
    );
  }, []);

  const updateRoutingDomain = useCallback(
    (domainId: string, patch: Partial<DraftRoutingDomain>) => {
      commit((prev) =>
        prev
          ? {
              ...prev,
              routing_domains: prev.routing_domains.map((domain) =>
                domain.domain_id === domainId ? { ...domain, ...patch } : domain,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeRoutingDomain = useCallback((domainId: string) => {
    commit((prev) =>
      prev
        ? {
            ...prev,
            routing_domains: prev.routing_domains.filter((d) => d.domain_id !== domainId),
          }
        : prev,
    );
  }, []);

  const updateBoundary = useCallback(
    (boundaryId: string, patch: Partial<DraftBoundary>) => {
      commit((prev) =>
        prev
          ? {
              ...prev,
              boundaries: prev.boundaries.map((boundary) =>
                boundary.boundary_id === boundaryId ? { ...boundary, ...patch } : boundary,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeBoundary = useCallback((boundaryId: string) => {
    commit((prev) =>
      prev
        ? { ...prev, boundaries: prev.boundaries.filter((b) => b.boundary_id !== boundaryId) }
        : prev,
    );
  }, []);

  /** Customize-a-block for ground: swap a placed reference for its fork. */
  const replaceGroundRefWithDraft = useCallback(
    (segmentId: string, draft: DraftGroundSet) => {
      commit((prev) =>
        prev
          ? {
              ...prev,
              ground_refs: prev.ground_refs.filter((r) => r.segment_id !== segmentId),
              ground: [...prev.ground, draft],
            }
          : prev,
      );
    },
    [],
  );

  const updateGroundDraft = useCallback(
    (segmentId: string, patch: Partial<DraftGroundSet>) => {
      commit((prev) =>
        prev
          ? {
              ...prev,
              ground: prev.ground.map((draft) =>
                draft.segment_id === segmentId ? { ...draft, ...patch } : draft,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeGroundDraft = useCallback((segmentId: string) => {
    commit((prev) =>
      prev
        ? { ...prev, ground: prev.ground.filter((d) => d.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  return {
    workspace,
    currentWorkspace,
    openWorkspace,
    commitWorkspace,
    updateSession,
    undo,
    close,
    removeRefSegment,
    removeConstellation,
    updateConstellation,
    removeGroundRef,
    replaceGroundRefWithDraft,
    updateGroundDraft,
    removeGroundDraft,
    updateLinkRule,
    removeLinkRule,
    updateRoutingDomain,
    removeRoutingDomain,
    updateBoundary,
    removeBoundary,
  };
}
