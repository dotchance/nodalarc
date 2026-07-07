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
  EARTH_BODY_REF,
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

// N9: one serialization owner for both slots. Writes carry a versioned
// envelope so a draft written by an older build migrates on restore instead
// of loading malformed; reads validate shape and never adopt garbage.
const ENVELOPE_VERSION = 1;

export type RestoreResult = { ok: true } | { ok: false; reason: string };

/** The one write serializer for autosave and backup. */
export function serializeWorkspace(workspace: Workspace): string {
  return JSON.stringify({ v: ENVELOPE_VERSION, workspace });
}

function _isWorkspaceShape(value: unknown): value is Workspace {
  if (!value || typeof value !== "object") return false;
  const w = value as Record<string, unknown>;
  const isObj = (v: unknown): v is Record<string, unknown> =>
    !!v && typeof v === "object" && !Array.isArray(v);
  // The containers must be arrays, AND the entries the migration walks must be
  // well-formed: each space draft carries an orbit object, each ground draft a
  // members array. A shape-valid-but-partial payload is unmigratable — refuse
  // it here rather than adopt a malformed workspace or let the migration throw.
  return (
    typeof w.name === "string" &&
    Array.isArray(w.space) &&
    w.space.every((d) => isObj(d) && isObj(d.orbit)) &&
    Array.isArray(w.space_refs) &&
    Array.isArray(w.ground) &&
    w.ground.every((d) => isObj(d) && Array.isArray(d.members)) &&
    Array.isArray(w.ground_refs) &&
    Array.isArray(w.links) &&
    Array.isArray(w.routing_domains) &&
    Array.isArray(w.boundaries)
  );
}

/** v0→v1: field-fill only. A draft authored under the Earth-only builder
 *  omits site.body/stamp.body/orbit.central_body; fill the Earth body ref
 *  (a migration default for those drafts, NOT a new-authoring default — it
 *  never touches the multi-body no-hardcode rule). Current-shape drafts,
 *  which already carry the fields, adopt unchanged. */
function _migrateV0toV1(workspace: Workspace): Workspace {
  // The shape gate guarantees each space draft has an orbit object and each
  // ground draft a members array, so this fills body refs without needing to
  // guard every access; _deserializeWorkspace's try/catch is the final belt.
  return {
    ...workspace,
    space: workspace.space.map((draft) => ({
      ...draft,
      orbit: { ...draft.orbit, central_body: draft.orbit.central_body ?? EARTH_BODY_REF },
    })),
    ground: workspace.ground.map((draft) => ({
      ...draft,
      stamp: { ...draft.stamp, body: draft.stamp?.body ?? EARTH_BODY_REF },
      members: draft.members.map((member) =>
        member.site
          ? { ...member, site: { ...member.site, body: member.site.body ?? EARTH_BODY_REF } }
          : member,
      ),
    })),
  };
}

/** The read inverse: detect the envelope (a top-level numeric `v` — impossible
 *  on a bare Workspace), treat ANY bare payload as v0, migrate, and validate
 *  shape before adopting. Unmigratable payloads refuse with a reason and are
 *  never adopted as a malformed workspace. */
function _deserializeWorkspace(raw: string): RestoreResult & { workspace?: Workspace } {
  // One try/catch spans parse, shape, AND migrate: any failure — unparsable,
  // wrong shape, or a partial payload the migration cannot walk — becomes a
  // typed refusal, never an escaping throw that the ok/reason contract would
  // otherwise leak past its callers.
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return { ok: false, reason: "the saved draft could not be read" };
    }
    const payload =
      typeof (parsed as { v?: unknown }).v === "number"
        ? (parsed as { workspace?: unknown }).workspace
        : parsed; // bare payload = v0
    if (!_isWorkspaceShape(payload)) {
      return { ok: false, reason: "draft from an older build could not be restored" };
    }
    return { ok: true, workspace: _migrateV0toV1(payload) };
  } catch {
    return { ok: false, reason: "the saved draft could not be read" };
  }
}

/** The canonical payload of a stored slot, version-independent — used to tell
 *  whether two drafts are the SAME (a stash need not refuse an identical
 *  backup) or DIFFERENT (a refuse-choice is owed). Null when the slot is
 *  absent or unreadable. */
function _slotPayload(raw: string | null): string | null {
  if (raw === null) return null;
  const result = _deserializeWorkspace(raw);
  return result.ok ? JSON.stringify(result.workspace) : null;
}

/** The outcome of a stash-before-displace: STASHED the current draft, SKIPPED
 *  (nothing worth preserving — pristine or empty), or REFUSED (a real,
 *  different backup already exists and would be lost). A refused stash blocks
 *  the displacing gesture until the user chooses to overwrite or cancel. */
export type StashOutcome = "stashed" | "skipped" | "refused";

/** Creation logic in one place: the seed workspace when none exists. Pure —
 *  any stash/storage side effect stays OUTSIDE the setWorkspace updater
 *  (StrictMode double-fires updater bodies). */
function ensureWorkspace(prev: Workspace | null, seedName: string): Workspace {
  return prev ?? newWorkspace(seedName);
}

/** A pristine-untitled draft is not a protectable draft: it is newWorkspace
 *  output with nothing authored. It must never trigger a stash (it cannot
 *  displace real work) and is freely overwritable as a backup. */
function _isPristineUntitled(workspace: Workspace): boolean {
  return (
    workspace.space.length === 0 &&
    workspace.space_refs.length === 0 &&
    workspace.ground.length === 0 &&
    workspace.ground_refs.length === 0 &&
    workspace.links.length === 0 &&
    workspace.routing_domains.length === 0 &&
    workspace.boundaries.length === 0
  );
}

export function useWorkspace() {
  const [workspace, setWorkspaceState] = useState<Workspace | null>(null);
  // The single mutation path (M4): sync workspaceRef SYNCHRONOUSLY so a
  // live-workspace read (M3's stash) never lags the commit — for BOTH the
  // value and updater forms — then set React state. Undo history and autosave
  // observe the state change as before.
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
    commit(past);
  }, []);

  useEffect(() => {
    if (!workspace) return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(AUTOSAVE_KEY, serializeWorkspace(workspace));
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

  /** Preserve the current draft to the backup slot before a gesture displaces
   *  it (M3: the LIVE workspace, not the up-to-800ms-stale autosave slot).
   *  Returns whether it stashed, skipped (nothing worth preserving), or
   *  refused (a real, different backup would be lost) so the caller can offer
   *  the overwrite/cancel choice. `force` takes the overwrite choice. */
  const stashAutosaveToBackup = useCallback(
    (opts?: { force?: boolean }): StashOutcome => {
      try {
        const current = workspaceRef.current;
        let raw: string | null;
        if (current !== null) {
          // A pristine-untitled current draft is not protectable — skip
          // entirely so it never bulldozes an existing backup.
          if (_isPristineUntitled(current)) return "skipped";
          raw = serializeWorkspace(current);
        } else {
          // No live workspace: preserve the prior autosave slot that the next
          // workspace's autosave would otherwise overwrite.
          raw = localStorage.getItem(AUTOSAVE_KEY);
          if (raw === null) return "skipped";
          const prior = _deserializeWorkspace(raw);
          if (prior.ok && prior.workspace && _isPristineUntitled(prior.workspace)) {
            return "skipped";
          }
        }
        const existing = localStorage.getItem(BACKUP_KEY);
        const existingRead = existing !== null ? _deserializeWorkspace(existing) : null;
        const existingPristine =
          existingRead?.ok && existingRead.workspace
            ? _isPristineUntitled(existingRead.workspace)
            : false;
        const currentPayload = _slotPayload(raw);
        const existingPayload = _slotPayload(existing);
        const sameDraft = currentPayload !== null && currentPayload === existingPayload;
        const freeToOverwrite =
          opts?.force === true ||
          existing === null ||
          existingRead?.ok !== true ||
          existingPristine ||
          sameDraft;
        if (!freeToOverwrite) return "refused";
        localStorage.setItem(BACKUP_KEY, raw);
        return "stashed";
      } catch {
        // Storage unavailable — nothing to preserve.
        return "skipped";
      }
    },
    [],
  );

  const hasBackup = useCallback((): boolean => {
    try {
      return localStorage.getItem(BACKUP_KEY) !== null;
    } catch {
      return false;
    }
  }, []);

  /** The self-ensuring creation path (M4): building a block with no workspace
   *  starts one. Pure — no side effect inside or around the updater (a side
   *  effect in an updater double-fires under StrictMode). Preserving the
   *  displaced draft (stash-if-null, with the refuse/overwrite choice) and
   *  clearing a refused-import world first (P2) are the CALLER's job:
   *  BuilderView owns the backup-choice dialog and the resolve world, and
   *  routes a create-from-null through `displace` so the prior draft is never
   *  silently lost when the backup slot is already occupied. */
  const createWorkspace = useCallback(
    (build: (workspace: Workspace) => Workspace) => {
      commit((prev) => build(ensureWorkspace(prev, "untitled-session")));
    },
    [commit],
  );

  /** Bring the displaced draft back as the workspace. The imported session
   *  it replaces is server truth and re-importable — nothing is lost. */
  const restoreBackup = useCallback((): RestoreResult => {
    let raw: string | null;
    try {
      raw = localStorage.getItem(BACKUP_KEY);
    } catch {
      raw = null;
    }
    if (raw === null) return { ok: false, reason: "there is no draft to restore" };
    const parsed = _deserializeWorkspace(raw);
    if (!parsed.ok || !parsed.workspace) {
      return { ok: false, reason: parsed.ok ? "the saved draft could not be read" : parsed.reason };
    }
    reseedCounters(parsed.workspace);
    // Consume the slot only on success — a draft that cannot be restored is
    // never silently destroyed.
    try {
      localStorage.removeItem(BACKUP_KEY);
    } catch {
      // Storage unavailable — the workspace still adopts.
    }
    commit(parsed.workspace);
    return { ok: true };
  }, [commit]);

  const restoreAutosave = useCallback((): RestoreResult => {
    let raw: string | null;
    try {
      raw = localStorage.getItem(AUTOSAVE_KEY);
    } catch {
      raw = null;
    }
    if (raw === null) return { ok: false, reason: "there is no draft to restore" };
    const parsed = _deserializeWorkspace(raw);
    if (!parsed.ok || !parsed.workspace) {
      return { ok: false, reason: parsed.ok ? "the saved draft could not be read" : parsed.reason };
    }
    // Fresh module counters would re-mint ids the restored draft already
    // uses — reseed past everything it carries.
    reseedCounters(parsed.workspace);
    commit(parsed.workspace);
    return { ok: true };
  }, [commit]);

  const discardAutosave = useCallback(() => {
    try {
      localStorage.removeItem(AUTOSAVE_KEY);
    } catch {
      // Nothing to discard when storage is unavailable.
    }
  }, []);

  const startNew = useCallback((name: string) => {
    commit(newWorkspace(name));
  }, []);

  /** Adopt a ready-made workspace (session import). The caller is
   *  responsible for id-counter reseeding (the importer does it). */
  const openWorkspace = useCallback((imported: Workspace) => {
    commit(imported);
  }, []);

  /** Atomic adoption of a next workspace the caller composed from the
   *  current one (apply-all-and-save, save-time rename). Rides the single
   *  mutation path: exactly one undo entry, normal autosave. Import
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
          | "name"
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

  // Library "use" gestures are self-ensuring: using a block with no open
  // workspace starts one - building never dead-ends on missing state.
  const addConstellation = useCallback(
    (nodeRef: string) => {
      createWorkspace((workspace) => ({
        ...workspace,
        space: [...workspace.space, newDraftConstellation(nodeRef)],
      }));
    },
    [createWorkspace],
  );

  const addConstellationRef = useCallback(
    (ref: string, label: string) => {
      createWorkspace((workspace) => ({
        ...workspace,
        space_refs: [...workspace.space_refs, newRefSegment(ref, label)],
      }));
    },
    [createWorkspace],
  );

  /** Add an already-built draft (a fork of a library block). */
  const addDraft = useCallback(
    (draft: DraftConstellation) => {
      createWorkspace((workspace) => ({ ...workspace, space: [...workspace.space, draft] }));
    },
    [createWorkspace],
  );

  const removeRefSegment = useCallback((segmentId: string) => {
    commit((prev) =>
      prev
        ? { ...prev, space_refs: prev.space_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  /** Customize-a-block: swap a placed reference for its forked draft. */
  const replaceRefWithDraft = useCallback(
    (segmentId: string, draft: DraftConstellation) => {
      commit((prev) =>
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

  const updateOrbit = useCallback(
    (segmentId: string, patch: Partial<DraftOrbit>) => {
      commit((prev) =>
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

  const addGroundRef = useCallback(
    (ref: string, label: string) => {
      createWorkspace((workspace) => ({
        ...workspace,
        ground_refs: [...workspace.ground_refs, newRefGroundSet(ref, label)],
      }));
    },
    [createWorkspace],
  );

  const updateGroundRef = useCallback(
    (segmentId: string, patch: Partial<RefGroundSet>) => {
      commit((prev) =>
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
    commit((prev) =>
      prev
        ? { ...prev, ground_refs: prev.ground_refs.filter((r) => r.segment_id !== segmentId) }
        : prev,
    );
  }, []);

  /** Add an authored (or forked) ground segment draft. */
  const addGroundDraft = useCallback(
    (draft: DraftGroundSet) => {
      createWorkspace((workspace) => ({ ...workspace, ground: [...workspace.ground, draft] }));
    },
    [createWorkspace],
  );

  /** Place a defined site into the last ground segment draft — self-ensuring:
   *  with no draft (or no workspace) open, makeDraft starts one, so using a
   *  site from the Library never dead-ends. */
  const addGroundMember = useCallback(
    (member: DraftGroundSite, makeDraft: () => DraftGroundSet) => {
      createWorkspace((workspace) => {
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
    [createWorkspace],
  );

  /** Connect two placed segments (self-ensuring is NOT needed here: a link
   *  rule requires segments, so the workspace always exists first). */
  const addLinkRule = useCallback((rule: DraftLinkRule) => {
    commit((prev) => (prev ? { ...prev, links: [...prev.links, rule] } : prev));
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

  const updateLinkEndpoint = useCallback(
    (ruleId: string, side: "a" | "b", patch: Partial<DraftLinkEndpoint>) => {
      commit((prev) =>
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
    commit((prev) =>
      prev ? { ...prev, links: prev.links.filter((rule) => rule.rule_id !== ruleId) } : prev,
    );
  }, []);

  const addRoutingDomain = useCallback((domain: DraftRoutingDomain) => {
    commit((prev) =>
      prev ? { ...prev, routing_domains: [...prev.routing_domains, domain] } : prev,
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

  const addBoundary = useCallback((boundary: DraftBoundary) => {
    commit((prev) =>
      prev ? { ...prev, boundaries: [...prev.boundaries, boundary] } : prev,
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
