// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace state: the applied session drafts plus undo and autosave.
 *
 *  The edit→resolve loop lives in BuilderView, which serializes the
 *  workspace with any open windows' working copies overlaid — the canvas
 *  previews what is being edited while the workspace itself only changes on
 *  Apply. The rendered world is always the resolver's expansion of that
 *  serialization — or the resolver's error, verbatim. No builder-local
 *  expansion, ever.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  identifier,
  newDraftConstellation,
  reseedCounters,
  newRefGroundSet,
  newRefSegment,
  newWorkspace,
  type DraftConstellation,
  type DraftGroundSet,
  type DraftGroundSite,
  type DraftBoundary,
  type DraftLinkEndpoint,
  type DraftLinkRule,
  type DraftRoutingDomain,
  type DraftOrbit,
  type RefGroundSet,
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
 *  was adopted cannot diverge. Buffer keys are `kind` or `kind:id`. */
export function overlayBuffers(workspace: Workspace, buffers: BufferMap): Workspace {
  let out = workspace;
  for (const [key, buf] of Object.entries(buffers)) {
    if (!buf.dirty) continue;
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

function _appliedObjectForKey(workspace: Workspace, key: string): unknown {
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

/** Dirty buffers whose applied object changed underneath them (undo,
 *  restore, deletion) since the window's `opened` base was taken. Bulk
 *  apply-and-save refuses these — a bulk gesture must never silently apply
 *  a stale working copy over an object the user reverted. */
export function staleBufferKeys(workspace: Workspace, buffers: BufferMap): string[] {
  const stale: string[] = [];
  for (const [key, buf] of Object.entries(buffers)) {
    if (!buf.dirty) continue;
    const kind = key.indexOf(":") === -1 ? key : key.slice(0, key.indexOf(":"));
    if (kind === "session") {
      const opened = buf.opened as Record<string, unknown> | null;
      if (!opened) continue;
      const pick = Object.fromEntries(
        Object.keys(opened).map((k) => [k, (workspace as unknown as Record<string, unknown>)[k]]),
      );
      if (!_bufferDeepEqual(pick, opened)) stale.push(key);
      continue;
    }
    const applied = _appliedObjectForKey(workspace, key);
    if (applied === null || !_bufferDeepEqual(applied, buf.opened)) stale.push(key);
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
  opts: { applyAll: boolean; dialogName: string; nameTouched: boolean },
): Workspace {
  const base = opts.applyAll ? overlayBuffers(workspace, buffers) : workspace;
  const name = opts.nameTouched ? identifier(opts.dialogName) || base.name : base.name;
  return base.name === name ? base : { ...base, name };
}

const AUTOSAVE_KEY = "nodalarc-builder-draft";
// The draft a running-session import displaced — preserved, never silently
// destroyed (autosave overwrites its own slot the moment a workspace
// exists, so displacement must copy first).
const BACKUP_KEY = "nodalarc-builder-draft-previous";
const AUTOSAVE_DEBOUNCE_MS = 800;
const HISTORY_LIMIT = 100;

export function useWorkspace() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);

  // Trust mechanics: every mutation lands in a bounded history (undo) and a
  // debounced localStorage autosave (restore-after-crash/refresh).
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
    setWorkspace(past);
  }, []);

  useEffect(() => {
    if (!workspace) return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(workspace));
      } catch {
        // Quota/private-mode failures must never break editing.
      }
    }, AUTOSAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [workspace]);

  /** True when an autosaved draft exists to offer on the start card. */
  const hasAutosave = useCallback((): boolean => {
    try {
      return localStorage.getItem(AUTOSAVE_KEY) !== null;
    } catch {
      return false;
    }
  }, []);

  /** Preserve the current autosaved draft before an import displaces it. */
  const stashAutosaveToBackup = useCallback(() => {
    try {
      const raw = localStorage.getItem(AUTOSAVE_KEY);
      if (raw !== null) localStorage.setItem(BACKUP_KEY, raw);
    } catch {
      // Storage unavailable — nothing to preserve.
    }
  }, []);

  const hasBackup = useCallback((): boolean => {
    try {
      return localStorage.getItem(BACKUP_KEY) !== null;
    } catch {
      return false;
    }
  }, []);

  /** Bring the displaced draft back as the workspace. The imported session
   *  it replaces is server truth and re-importable — nothing is lost. */
  const restoreBackup = useCallback((): boolean => {
    try {
      const raw = localStorage.getItem(BACKUP_KEY);
      if (!raw) return false;
      const restored = JSON.parse(raw) as Workspace;
      reseedCounters(restored);
      localStorage.removeItem(BACKUP_KEY);
      setWorkspace(restored);
      return true;
    } catch {
      return false;
    }
  }, []);

  const restoreAutosave = useCallback((): boolean => {
    try {
      const raw = localStorage.getItem(AUTOSAVE_KEY);
      if (!raw) return false;
      const restored = JSON.parse(raw) as Workspace;
      // Fresh module counters would re-mint ids the restored draft already
      // uses — reseed past everything it carries.
      reseedCounters(restored);
      setWorkspace(restored);
      return true;
    } catch {
      return false;
    }
  }, []);

  const discardAutosave = useCallback(() => {
    try {
      localStorage.removeItem(AUTOSAVE_KEY);
    } catch {
      // Nothing to discard when storage is unavailable.
    }
  }, []);

  const startNew = useCallback((name: string) => {
    setWorkspace(newWorkspace(name));
  }, []);

  /** Adopt a ready-made workspace (session import). The caller is
   *  responsible for id-counter reseeding (the importer does it). */
  const openWorkspace = useCallback((imported: Workspace) => {
    setWorkspace(imported);
  }, []);

  /** Atomic adoption of a next workspace the caller composed from the
   *  current one (apply-all-and-save, save-time rename). Rides the single
   *  mutation path: exactly one undo entry, normal autosave. Import
   *  adoption stays `openWorkspace`; apply-all must not abuse it. */
  const commitWorkspace = useCallback((next: Workspace, _reason: string) => {
    setWorkspace(next);
  }, []);

  /** Session-level plumbing: name, time, and the candidate budget. */
  const updateSession = useCallback(
    (
      patch: Partial<
        Pick<
          Workspace,
          | "name"
          | "start_time"
          | "step_seconds"
          | "compression"
          | "max_pairs_per_rule"
          | "max_pairs_per_tick"
        >
      >,
    ) => {
      setWorkspace((prev) => (prev ? { ...prev, ...patch } : prev));
    },
    [],
  );

  const close = useCallback(() => setWorkspace(null), []);

  // Library "use" gestures are self-ensuring: using a block with no open
  // workspace starts one - building never dead-ends on missing state.
  const addConstellation = useCallback((nodeRef: string) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, space: [...workspace.space, newDraftConstellation(nodeRef)] };
    });
  }, []);

  const addConstellationRef = useCallback((ref: string, label: string) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, space_refs: [...workspace.space_refs, newRefSegment(ref, label)] };
    });
  }, []);

  /** Add an already-built draft (a fork of a library block). */
  const addDraft = useCallback((draft: DraftConstellation) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, space: [...workspace.space, draft] };
    });
  }, []);

  const removeRefSegment = useCallback((segmentId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, space_refs: prev.space_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  /** Customize-a-block: swap a placed reference for its forked draft. */
  const replaceRefWithDraft = useCallback(
    (segmentId: string, draft: DraftConstellation) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              space_refs: prev.space_refs.filter((r) => r.segment_id !== segmentId),
              space: [...prev.space, draft],
            }
          : prev,
      );
    },
    [],
  );

  const removeConstellation = useCallback((segmentId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, space: prev.space.filter((d) => d.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  const updateConstellation = useCallback(
    (segmentId: string, patch: Partial<DraftConstellation>) => {
      setWorkspace((prev) =>
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

  const updateOrbit = useCallback(
    (segmentId: string, patch: Partial<DraftOrbit>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              space: prev.space.map((draft) =>
                draft.segment_id === segmentId
                  ? { ...draft, orbit: { ...draft.orbit, ...patch } }
                  : draft,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const addGroundRef = useCallback((ref: string, label: string) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, ground_refs: [...workspace.ground_refs, newRefGroundSet(ref, label)] };
    });
  }, []);

  const updateGroundRef = useCallback(
    (segmentId: string, patch: Partial<RefGroundSet>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              ground_refs: prev.ground_refs.map((placed) =>
                placed.segment_id === segmentId ? { ...placed, ...patch } : placed,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeGroundRef = useCallback((segmentId: string) => {
    setWorkspace((prev) =>
      prev
        ? { ...prev, ground_refs: prev.ground_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  /** Add an authored (or forked) ground segment draft. */
  const addGroundDraft = useCallback((draft: DraftGroundSet) => {
    setWorkspace((prev) => {
      const workspace = prev ?? newWorkspace("untitled-session");
      return { ...workspace, ground: [...workspace.ground, draft] };
    });
  }, []);

  /** Place a defined site into the last ground segment draft — self-ensuring:
   *  with no draft (or no workspace) open, makeDraft starts one, so using a
   *  site from the Library never dead-ends. */
  const addGroundMember = useCallback(
    (member: DraftGroundSite, makeDraft: () => DraftGroundSet) => {
      setWorkspace((prev) => {
        const workspace = prev ?? newWorkspace("untitled-session");
        if (workspace.ground.length === 0) {
          const draft = makeDraft();
          return { ...workspace, ground: [{ ...draft, members: [member] }] };
        }
        const last = workspace.ground[workspace.ground.length - 1] as DraftGroundSet;
        return {
          ...workspace,
          ground: workspace.ground.map((draft) =>
            draft === last ? { ...draft, members: [...draft.members, member] } : draft,
          ),
        };
      });
    },
    [],
  );

  /** Connect two placed segments (self-ensuring is NOT needed here: a link
   *  rule requires segments, so the workspace always exists first). */
  const addLinkRule = useCallback((rule: DraftLinkRule) => {
    setWorkspace((prev) => (prev ? { ...prev, links: [...prev.links, rule] } : prev));
  }, []);

  const updateLinkRule = useCallback((ruleId: string, patch: Partial<DraftLinkRule>) => {
    setWorkspace((prev) =>
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

  const updateLinkEndpoint = useCallback(
    (ruleId: string, side: "a" | "b", patch: Partial<DraftLinkEndpoint>) => {
      setWorkspace((prev) =>
        prev
          ? {
              ...prev,
              links: prev.links.map((rule) =>
                rule.rule_id === ruleId
                  ? { ...rule, [side]: { ...rule[side], ...patch } }
                  : rule,
              ),
            }
          : prev,
      );
    },
    [],
  );

  const removeLinkRule = useCallback((ruleId: string) => {
    setWorkspace((prev) =>
      prev ? { ...prev, links: prev.links.filter((rule) => rule.rule_id !== ruleId) } : prev,
    );
  }, []);

  const addRoutingDomain = useCallback((domain: DraftRoutingDomain) => {
    setWorkspace((prev) =>
      prev ? { ...prev, routing_domains: [...prev.routing_domains, domain] } : prev,
    );
  }, []);

  const updateRoutingDomain = useCallback(
    (domainId: string, patch: Partial<DraftRoutingDomain>) => {
      setWorkspace((prev) =>
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
    setWorkspace((prev) =>
      prev
        ? {
            ...prev,
            routing_domains: prev.routing_domains.filter((d) => d.domain_id !== domainId),
          }
        : prev,
    );
  }, []);

  const addBoundary = useCallback((boundary: DraftBoundary) => {
    setWorkspace((prev) =>
      prev ? { ...prev, boundaries: [...prev.boundaries, boundary] } : prev,
    );
  }, []);

  const updateBoundary = useCallback(
    (boundaryId: string, patch: Partial<DraftBoundary>) => {
      setWorkspace((prev) =>
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
    setWorkspace((prev) =>
      prev
        ? { ...prev, boundaries: prev.boundaries.filter((b) => b.boundary_id !== boundaryId) }
        : prev,
    );
  }, []);

  /** Customize-a-block for ground: swap a placed reference for its fork. */
  const replaceGroundRefWithDraft = useCallback(
    (segmentId: string, draft: DraftGroundSet) => {
      setWorkspace((prev) =>
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
      setWorkspace((prev) =>
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
    setWorkspace((prev) =>
      prev
        ? { ...prev, ground: prev.ground.filter((d) => d.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  return {
    workspace,
    startNew,
    openWorkspace,
    commitWorkspace,
    updateSession,
    undo,
    hasAutosave,
    restoreAutosave,
    stashAutosaveToBackup,
    hasBackup,
    restoreBackup,
    discardAutosave,
    close,
    addConstellation,
    addConstellationRef,
    addDraft,
    removeRefSegment,
    replaceRefWithDraft,
    removeConstellation,
    updateConstellation,
    updateOrbit,
    addGroundRef,
    updateGroundRef,
    removeGroundRef,
    addGroundDraft,
    addGroundMember,
    replaceGroundRefWithDraft,
    updateGroundDraft,
    removeGroundDraft,
    addLinkRule,
    updateLinkRule,
    updateLinkEndpoint,
    removeLinkRule,
    addRoutingDomain,
    updateRoutingDomain,
    removeRoutingDomain,
    addBoundary,
    updateBoundary,
    removeBoundary,
  };
}
