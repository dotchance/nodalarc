// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The builder's floating-editor windows and their buffered edits.
 *
 *  Extracted from BuilderView: the diagram workspace is a set of floating,
 *  per-object editor windows, each editing a copy (buffer) of its object that
 *  the session only adopts on Apply. This hook owns the windows list, the
 *  buffer map, and the reconciliation that keeps open windows honest when
 *  the applied workspace moves underneath them. The pure buffer maths
 *  (overlayBuffers/staleBufferKeys/workspaceForSave) stay in useWorkspace; this
 *  hook is the stateful shell that drives them.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { raiseWindow } from "../ui/windowStack";
import {
  overlayBuffers,
  staleBufferKeys,
  appliedObjectForKey,
  bufferAppliedChanged,
  type BufferMap,
  type EditorBuffer,
} from "./useWorkspace";
import type {
  DraftBoundary,
  DraftConstellation,
  DraftGroundSet,
  DraftLinkRule,
  DraftRoutingDomain,
  Workspace,
} from "./workspace";

/** A floating editor window's target — one per object, keyed. */
export type EditorTarget =
  | { kind: "session" }
  | { kind: "segment"; id: string }
  | { kind: "ground"; id: string }
  | { kind: "link"; id: string }
  | { kind: "domain"; id: string }
  | { kind: "boundary"; id: string }
  | { kind: "inspect"; ref: string; document: Record<string, unknown> }
  | { kind: "node-view"; nodeId: string }
  | { kind: "library" }
  | { kind: "catalog" }
  | { kind: "open-session" }
  | { kind: "source-yaml" }
  | { kind: "customize-chain"; segmentId: string; rootRef: string }
  | { kind: "save-session" };

export interface EditorWindow {
  key: string;
  target: EditorTarget;
  x: number;
  y: number;
}

export interface EditorWindowsRecoveryState {
  windows: EditorWindow[];
  buffers: BufferMap;
}

export function targetKey(target: EditorTarget): string {
  switch (target.kind) {
    case "session":
    case "library":
    case "catalog":
    case "open-session":
    case "source-yaml":
    case "save-session":
      return target.kind;
    case "customize-chain":
      return `customize:${target.segmentId}`;
    case "inspect":
      return `inspect:${target.ref}`;
    case "node-view":
      return `node:${target.nodeId}`;
    default:
      return `${target.kind}:${target.id}`;
  }
}

/** A human title for a window's object — the name shown in the stale-window
 *  confirm list. Reads the object's own display name/label, falling back to the
 *  id; the session window names the session. */
function objectTitle(workspace: Workspace, target: EditorTarget): string {
  switch (target.kind) {
    case "session":
      return `Session · ${workspace.session_name}`;
    case "segment":
      return (
        workspace.space.find((d) => d.segment_id === target.id)?.display_name ?? target.id
      );
    case "ground":
      return (
        workspace.ground.find((d) => d.segment_id === target.id)?.display_name ?? target.id
      );
    case "link": {
      const rule = workspace.links.find((r) => r.rule_id === target.id);
      return rule?.label || target.id;
    }
    case "domain":
      return (
        workspace.routing_domains.find((d) => d.domain_id === target.id)?.label ?? target.id
      );
    case "boundary":
      return "Boundary";
    default:
      return targetKey(target);
  }
}

/** A window bound to a workspace object — one the reconciliation pass can
 *  resolve through appliedObjectForKey and must prune when it vanishes. The
 *  seven other kinds (session's applied view is a field-pick, not an object;
 *  library/catalog/open-session/save-session are chrome; inspect/node-view are
 *  read-only views of catalog/world state) own no editable object and are never
 *  pruned or re-seeded by the pass. */
function isObjectTarget(target: EditorTarget): boolean {
  switch (target.kind) {
    case "segment":
    case "ground":
    case "link":
    case "domain":
    case "boundary":
      return true;
    default:
      return false;
  }
}

export type SessionBuffer = Pick<
  Workspace,
  | "session_name"
  | "start_time"
  | "step_seconds"
  | "compression"
  | "max_pairs_per_rule"
  | "max_pairs_per_tick"
>;

/** The workspace mutators applyBuffer commits into. */
interface UseEditorWindowsDeps {
  workspace: Workspace | null;
  updateSession: (patch: SessionBuffer) => void;
  updateConstellation: (segmentId: string, draft: DraftConstellation) => void;
  updateGroundDraft: (segmentId: string, draft: DraftGroundSet) => void;
  updateLinkRule: (ruleId: string, draft: DraftLinkRule) => void;
  updateRoutingDomain: (domainId: string, draft: DraftRoutingDomain) => void;
  updateBoundary: (boundaryId: string, draft: DraftBoundary) => void;
}

export function useEditorWindows({
  workspace,
  updateSession,
  updateConstellation,
  updateGroundDraft,
  updateLinkRule,
  updateRoutingDomain,
  updateBoundary,
}: UseEditorWindowsDeps) {
  // The diagram workspace: editors are floating, anchored windows — many
  // can be open at once, keyed per object (re-open focuses, ).
  const [windows, setWindows] = useState<EditorWindow[]>([]);
  const [buffers, setBuffers] = useState<BufferMap>({});
  const bufferMutationRevisionRef = useRef(0);
  const markBufferMutation = () => {
    bufferMutationRevisionRef.current += 1;
  };
  const currentBufferMutationRevision = () => bufferMutationRevisionRef.current;
  const openEditor = (target: EditorTarget) => {
    const key = targetKey(target);
    // Re-open FOCUSES via the one stacking mechanism — the raise stack,
    // not an array reorder. The side effect stays outside the updater (StrictMode
    // double-invokes updater bodies); a fresh window rises on mount instead.
    if (windows.some((w) => w.key === key)) raiseWindow(key);
    setWindows((prev) => {
      const existing = prev.find((w) => w.key === key);
      if (existing) {
        // Refresh the target payload IN PLACE — array order no longer decides
        // stacking, so the window keeps its slot and the raise stack owns z.
        return prev.map((w) => (w.key === key ? { ...w, target } : w));
      }
      const n = prev.length;
      // Spawn cascade: monotonic and clamped (the old %6 repeated exactly every
      // sixth window); a far-out window is pulled back so it stays on screen.
      const step = Math.min(n, 8);
      return [...prev, { key, target, x: 440 + step * 40, y: 84 + step * 32 }];
    });
  };
  /** Every window teardown goes through here: closing a window removes it
   *  AND its buffer, always together. */
  const closeWindows = (predicate: (w: EditorWindow) => boolean) => {
    const closingWindows = windows.filter(predicate);
    const closing = new Set(closingWindows.map((w) => w.key));
    if (closing.size === 0) return;
    setWindows((prev) => prev.filter((w) => !closing.has(w.key)));
    markBufferMutation();
    setBuffers((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const key of closing) {
        if (key in next) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  };
  const closeWindow = (key: string) => closeWindows((w) => w.key === key);
  const closeAllWindows = () => closeWindows(() => true);
  const isOpen = (key: string) => windows.some((w) => w.key === key);

  // Buffered editing: an editor window works on a copy of its object; the
  // session only changes on Apply/OK. Cancel and the title-bar X discard —
  // the window says which state it is in, so closing is never a guess.
  /** First edit creates the buffer from the object as rendered ("base");
   *  later edits build on the working copy. "opened" — the Defaults target —
   *  is the window's baseline: the values at window open, advanced to the
   *  applied draft on each Apply (see applyBuffer). The session buffer is
   *  a pick, never the whole workspace: applying a stale whole-workspace
   *  clone would silently revert every other window's applied work. */
  const patchBuffer = <T,>(key: string, base: T, fn: (draft: T) => T) => {
    markBufferMutation();
    setBuffers((prev) => {
      const buf = prev[key];
      const current = (buf?.draft as T | undefined) ?? structuredClone(base);
      const opened = buf?.opened ?? structuredClone(base);
      return { ...prev, [key]: { draft: fn(current), opened, dirty: true } };
    });
  };
  const revertBuffer = (key: string) => {
    markBufferMutation();
    setBuffers((prev) => {
      const buf = prev[key];
      if (!buf) return prev;
      // Defaults = the baseline: the values at window open, advanced to the
      // applied draft on each Apply. On a stale window (the applied object moved
      // underneath), restoring `opened` still does not
      // match the applied object, so the buffer stays dirty+stale: the notice
      // and Apply/"Load current values" persist, the reconciliation pass never
      // drops it, and non-applied values are never relabeled "applied".
      const stillStale = !!workspace && bufferAppliedChanged(workspace, key, buf);
      return {
        ...prev,
        [key]: { ...buf, draft: structuredClone(buf.opened), dirty: stillStale },
      };
    });
  };
  const applyBuffer = (target: EditorTarget) => {
    const key = targetKey(target);
    const buf = buffers[key];
    if (!buf || !buf.dirty) return;
    switch (target.kind) {
      case "session":
        updateSession(buf.draft as SessionBuffer);
        break;
      case "segment":
        updateConstellation(target.id, buf.draft as DraftConstellation);
        break;
      case "ground":
        updateGroundDraft(target.id, buf.draft as DraftGroundSet);
        break;
      case "link":
        updateLinkRule(target.id, buf.draft as DraftLinkRule);
        break;
      case "domain":
        updateRoutingDomain(target.id, buf.draft as DraftRoutingDomain);
        break;
      case "boundary":
        updateBoundary(target.id, buf.draft as DraftBoundary);
        break;
      default:
        return;
    }
    markBufferMutation();
    setBuffers((prev) => {
      const cur = prev[key];
      if (!cur) return prev;
      return {
        ...prev,
        [key]: { ...cur, opened: structuredClone(cur.draft), dirty: false },
      };
    });
  };
  /** The session as the canvas should show it: the applied workspace with
   *  every dirty window's working copy substituted in. Editing previews live
   *  (drag a slider, the sats move); the workspace itself still only changes
   *  on Apply. */
  const previewWorkspace = (): Workspace | null =>
    workspace ? overlayBuffers(workspace, buffers) : null;
  const dirtyWindows = Object.values(buffers).filter((b) => b.dirty).length;
  // reconcile open editors when the applied workspace moves underneath them
  // (undo, restore, a sibling edit, a deletion). Two disjoint responses:
  //  - GONE: a window whose object no longer exists (deleted, or an undone
  //    create) is pruned with its buffer — dirty or clean. The object is gone;
  //    there is nothing left to edit or apply into, and an unapplied buffer over
  //    a vanished object is not recoverable state.
  //  - MOVED-but-present: a CLEAN buffer whose applied object changed is dropped
  //    and lazily re-seeded from current values on the next edit (its "applied"
  //    label would otherwise be a lie); a DIRTY one survives, and staleKeys
  //    surfaces it with a notice plus "Load current values" so unsaved work is
  //    never silently discarded while its object still exists.
  // The reads are pure and outside the updaters (StrictMode double-invokes
  // updater bodies).
  useEffect(() => {
    if (!workspace) return;
    const gone = new Set(
      windows
        .filter(
          (w) =>
            isObjectTarget(w.target) &&
            appliedObjectForKey(workspace, targetKey(w.target)) === null,
        )
        .map((w) => w.key),
    );
    const dropClean = Object.entries(buffers)
      .filter(([key, buf]) => !buf.dirty && bufferAppliedChanged(workspace, key, buf))
      .map(([key]) => key);
    if (gone.size === 0 && dropClean.length === 0) return;
    if (gone.size > 0) setWindows((prev) => prev.filter((w) => !gone.has(w.key)));
    const drop = new Set([...gone, ...dropClean]);
    markBufferMutation();
    setBuffers((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const key of drop) {
        if (key in next) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [workspace, windows, buffers]);
  // The keys of every DIRTY buffer whose applied object moved underneath it —
  // THE single owner (per-window notice, the save-dialog block, and the stale
  // count all read this one memo, so they can never disagree).
  const staleKeys = useMemo(
    () => (workspace ? new Set(staleBufferKeys(workspace, buffers)) : new Set<string>()),
    [workspace, buffers],
  );
  // The stale windows the bulk apply-and-save confirm flow lists, each named by
  // its object. Every stale key has an open window (a buffer cannot outlive its
  // window); the fallback to the key is defensive only.
  const staleList = useMemo(() => {
    if (!workspace) return [];
    return [...staleKeys].map((key) => {
      const win = windows.find((w) => w.key === key);
      return { key, title: win ? objectTitle(workspace, win.target) : key };
    });
  }, [workspace, staleKeys, windows]);
  /** Replace a stale window's working copy with the object's current applied
   *  values (the deliberate opposite of Defaults, which returns to the window's
   *  baseline — the values at window open, advanced to the applied draft on each
   *  Apply). Only reachable while the object still exists — a deleted object's
   *  window is already pruned. */
  const loadCurrentValues = (target: EditorTarget) => {
    if (!workspace) return;
    const key = targetKey(target);
    let current: unknown;
    if (target.kind === "session") {
      const w = workspace as unknown as Record<string, unknown>;
      const opened = buffers[key]?.opened as Record<string, unknown> | undefined;
      const fields = opened ? Object.keys(opened) : [];
      current = Object.fromEntries(fields.map((k) => [k, w[k]]));
    } else {
      current = appliedObjectForKey(workspace, key);
    }
    if (current === null || current === undefined) return;
    markBufferMutation();
    setBuffers((prev) => {
      if (!(key in prev)) return prev;
      return {
        ...prev,
        [key]: {
          opened: structuredClone(current),
          draft: structuredClone(current),
          dirty: false,
        },
      };
    });
  };
  /** After a bulk apply-and-save, drop exactly the buffers this save overlaid —
   *  matched by identity, captured BEFORE the network round-trip. Anything
   *  created or re-edited during the await has a fresh identity and survives. */
  const dropAppliedBuffers = (applied: Map<string, EditorBuffer>) => {
    markBufferMutation();
    setBuffers((prev) => {
      const kept: BufferMap = {};
      for (const [k, b] of Object.entries(prev)) {
        if (applied.get(k) !== b) kept[k] = b;
      }
      return kept;
    });
  };
  const restoreRecoveryState = (state: EditorWindowsRecoveryState) => {
    setWindows(structuredClone(state.windows));
    markBufferMutation();
    setBuffers(structuredClone(state.buffers));
  };

  return {
    windows,
    openEditor,
    closeWindow,
    closeAllWindows,
    isOpen,
    buffers,
    currentBufferMutationRevision,
    patchBuffer,
    revertBuffer,
    applyBuffer,
    previewWorkspace,
    dirtyWindows,
    staleKeys,
    staleList,
    loadCurrentValues,
    dropAppliedBuffers,
    restoreRecoveryState,
  };
}
