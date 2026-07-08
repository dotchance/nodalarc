// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Entry lifecycle and draft preservation (P3). A draft is user work; silent
 *  destruction is the worst failure this UI can have. These pins cover the
 *  hook layer: N9's versioned envelope + migration + refuse-with-reason, M3's
 *  live-workspace stash, M4's stash-on-first-workspace, and the backup
 *  refuse/choice rules (a real different backup is never bulldozed; a pristine
 *  draft never protects itself nor bulldozes real work). The Live<->Builder
 *  toggle-preserves-everything behavior is drive-verified. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWorkspace, serializeWorkspace } from "../useWorkspace";
import {
  EARTH_BODY_REF,
  SCHEDULING_PRESETS,
  defaultLinkRule,
  defaultRoutingDomain,
  identifier,
  newDraftGroundSet,
  placedSegments,
  refGroundMember,
  siteSetObjectFromDraft,
  siteSetWrapperFromDraft,
  toSessionDocument,
  type DraftGroundSet,
  type Workspace,
} from "../workspace";

const AUTOSAVE_KEY = "nodalarc-builder-draft";
const BACKUP_KEY = "nodalarc-builder-draft-previous";
const SPACE_NODE = "nodalarc:nodes/space/x.yaml";
const GROUND_NODE = "nodalarc:nodes/ground/gw.yaml";

/** A real authored workspace (one constellation) built through the hook. */
function authoredWorkspace(name: string): Workspace {
  const { result } = renderHook(() => useWorkspace());
  act(() => result.current.startNew(name));
  act(() => result.current.addConstellation(SPACE_NODE));
  return result.current.workspace as Workspace;
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe("N9 — versioned envelope, migration, and refuse-with-reason", () => {
  it("(1) autosave writes a versioned {v, workspace} envelope", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.startNew("env-test"));
    act(() => result.current.addConstellation(SPACE_NODE));
    act(() => {
      vi.advanceTimersByTime(900); // flush the 800ms debounce
    });
    const raw = localStorage.getItem(AUTOSAVE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.v).toBe(1);
    expect(parsed.workspace.name).toBe("env-test");
    expect(parsed.workspace.space).toHaveLength(1);
  });

  it("(2) restores a bare pre-body draft, filling the Earth body ref (v0→v1)", () => {
    const authored = authoredWorkspace("old");
    // A BARE (un-enveloped, v0) payload with the body field stripped — exactly
    // a draft written by the Earth-only builder before the multi-body fields.
    const bare = JSON.parse(JSON.stringify(authored)) as Record<string, unknown>;
    delete ((bare.space as Record<string, unknown>[])[0]!.orbit as Record<string, unknown>)
      .central_body;
    localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(bare)); // no envelope

    const { result } = renderHook(() => useWorkspace());
    let outcome: unknown;
    act(() => {
      outcome = result.current.restoreAutosave();
    });
    expect(outcome).toEqual({ ok: true });
    expect(result.current.workspace?.space[0]?.orbit.central_body).toBe(EARTH_BODY_REF);
  });

  it("(3) refuses a garbage draft with a reason and preserves the stored value", () => {
    localStorage.setItem(BACKUP_KEY, "not json at all {");
    const { result } = renderHook(() => useWorkspace());
    let outcome: { ok: boolean; reason?: string } = { ok: true };
    act(() => {
      outcome = result.current.restoreBackup();
    });
    expect(outcome.ok).toBe(false);
    expect(outcome.reason).toMatch(/could not be read|older build/);
    expect(result.current.workspace).toBeNull();
    // The corrupt slot is never silently destroyed.
    expect(localStorage.getItem(BACKUP_KEY)).toBe("not json at all {");
  });

  it("(4) refuses a shape-valid-but-partial payload without throwing; slot survives", () => {
    // All eight top-level arrays present, but a ground entry the migration
    // would walk has no members array. Must refuse cleanly (no escaping throw).
    const partial = {
      name: "x",
      start_time: "2026-01-01T00:00:00Z",
      step_seconds: 1,
      compression: 1,
      max_pairs_per_rule: 1000,
      max_pairs_per_tick: 2000,
      space: [],
      space_refs: [],
      ground: [{}],
      ground_refs: [],
      links: [],
      routing_domains: [],
      boundaries: [],
    };
    localStorage.setItem(BACKUP_KEY, JSON.stringify(partial));
    const { result } = renderHook(() => useWorkspace());
    let outcome: { ok: boolean; reason?: string } = { ok: true };
    act(() => {
      outcome = result.current.restoreBackup();
    });
    expect(outcome.ok).toBe(false); // a typed refusal, never a thrown TypeError
    expect(outcome.reason).toMatch(/older build|could not be read/);
    expect(result.current.workspace).toBeNull();
    expect(localStorage.getItem(BACKUP_KEY)).not.toBeNull(); // slot survives
  });
});

describe("M3/M4 — the stash preserves the live draft, on time", () => {
  it("(M3) stashes the LIVE workspace, capturing an edit before the 800ms autosave", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.startNew("live"));
    act(() => result.current.addConstellation(SPACE_NODE));
    // Deliberately do NOT advance timers: the debounced autosave slot is still
    // empty/stale. The stash must read the live workspace, not that slot.
    let outcome: unknown;
    act(() => {
      outcome = result.current.stashAutosaveToBackup();
    });
    expect(outcome).toBe("stashed");
    const parsed = JSON.parse(localStorage.getItem(BACKUP_KEY)!);
    expect(parsed.workspace.space).toHaveLength(1); // the sub-800ms edit survived
  });

  it("(M4) the stash preserves the prior autosave slot when no workspace is open", () => {
    // The mechanism a self-ensuring creation-from-null routes through
    // (BuilderView's ensureThenCreate -> displace): with no live workspace the
    // stash falls back to the prior autosave slot, preserving that draft to the
    // backup before a new workspace would overwrite the autosave slot.
    localStorage.setItem(AUTOSAVE_KEY, serializeWorkspace(authoredWorkspace("prior")));
    const { result } = renderHook(() => useWorkspace()); // workspace === null
    let outcome: unknown;
    act(() => {
      outcome = result.current.stashAutosaveToBackup();
    });
    expect(outcome).toBe("stashed");
    expect(JSON.parse(localStorage.getItem(BACKUP_KEY)!).workspace.name).toBe("prior");
  });
});

describe("backup refuse/choice — never bulldoze real work, never protect pristine", () => {
  it("refuses to overwrite a real, different backup; force overwrites", () => {
    localStorage.setItem(BACKUP_KEY, serializeWorkspace(authoredWorkspace("draft-b")));
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.startNew("draft-a"));
    act(() => result.current.addConstellation(SPACE_NODE));

    let outcome: unknown;
    act(() => {
      outcome = result.current.stashAutosaveToBackup();
    });
    expect(outcome).toBe("refused");
    // The existing backup is untouched by a refused stash.
    expect(JSON.parse(localStorage.getItem(BACKUP_KEY)!).workspace.name).toBe("draft-b");

    act(() => {
      outcome = result.current.stashAutosaveToBackup({ force: true });
    });
    expect(outcome).toBe("stashed");
    expect(JSON.parse(localStorage.getItem(BACKUP_KEY)!).workspace.name).toBe("draft-a");
  });

  it("a pristine-untitled CURRENT draft never stashes — the backup survives", () => {
    const backup = serializeWorkspace(authoredWorkspace("draft-b"));
    localStorage.setItem(BACKUP_KEY, backup);
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.startNew("untitled-session")); // pristine, nothing authored
    let outcome: unknown;
    act(() => {
      outcome = result.current.stashAutosaveToBackup();
    });
    expect(outcome).toBe("skipped");
    expect(localStorage.getItem(BACKUP_KEY)).toBe(backup);
  });

  it("a pristine-untitled BACKUP is freely overwritten without refusal", () => {
    const { result: pristine } = renderHook(() => useWorkspace());
    act(() => pristine.current.startNew("untitled-session"));
    localStorage.setItem(BACKUP_KEY, serializeWorkspace(pristine.current.workspace as Workspace));

    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.startNew("draft-a"));
    act(() => result.current.addConstellation(SPACE_NODE));
    let outcome: unknown;
    act(() => {
      outcome = result.current.stashAutosaveToBackup();
    });
    expect(outcome).toBe("stashed");
    expect(JSON.parse(localStorage.getItem(BACKUP_KEY)!).workspace.name).toBe("draft-a");
  });
});

describe("convergeGroundToRef — close-time D7 convergence (P7g)", () => {
  const REF = "user:site-sets/denver-set.yaml";
  /** A workspace whose one authored ground set is BOTH a link-rule endpoint and
   *  a routing-domain member — the identity-continuity fixture. The set is
   *  ref-expressible (default name, a ref member, no session-owned blocks). */
  function convergeableWorkspace(): { ws: Workspace; ground: DraftGroundSet } {
    const built = ((): Workspace => {
      const { result } = renderHook(() => useWorkspace());
      act(() => result.current.startNew("d7-converge"));
      act(() => result.current.addConstellation(SPACE_NODE));
      return result.current.workspace as Workspace;
    })();
    const ground = newDraftGroundSet(GROUND_NODE, {});
    // A NON-default preset so the identity pin's carry-over assertions actually
    // distinguish "carried from the draft" from "silently defaulted" (both the
    // mint and the RefGroundSet default are leo-fast-handover).
    ground.scheduling_preset = "geo-longest-pass";
    ground.members = [refGroundMember("nodalarc:sites/denver.yaml", "denver", "Denver", null)];
    built.ground.push(ground);
    const placed = placedSegments(built);
    const space = placed.find((s) => s.kind === "space")!;
    const groundSeg = placed.find((s) => s.kind === "ground")!;
    built.links.push(defaultLinkRule(groundSeg, space));
    const domain = defaultRoutingDomain(built);
    domain.member_segment_ids = [ground.segment_id]; // single-member → bare segment selector
    built.routing_domains.push(domain);
    return { ws: built, ground };
  }

  const emittedRefsSegment = (segId: string) => (doc: any) =>
    doc.segments.find((s: any) => s.id === identifier(segId) && s.placement);
  const selectorNames = (sel: any): string[] =>
    sel.segment ? [sel.segment] : (sel.all ?? sel.any ?? []).map((x: any) => x.segment);

  it("(identity) reuses the segment_id and carries the preset, keeping rule + domain live", () => {
    const { ws, ground } = convergeableWorkspace();
    const snapshot = siteSetWrapperFromDraft(ground);
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(ws));
    act(() => result.current.convergeGroundToRef(ground.segment_id, REF, snapshot));

    // The set left `ground` for `ground_refs` under the SAME segment_id.
    expect(result.current.workspace?.ground.some((d) => d.segment_id === ground.segment_id)).toBe(
      false,
    );
    const converged = result.current.workspace?.ground_refs.find(
      (r) => r.segment_id === ground.segment_id,
    );
    expect(converged).toEqual({
      segment_id: ground.segment_id,
      ref: REF,
      label: ground.display_name,
      scheduling_preset: ground.scheduling_preset,
    });

    // In the emitted artifact both references stay live and the scheduling block
    // is the draft's own preset — not a silently defaulted one.
    const doc = toSessionDocument(result.current.workspace as Workspace) as any;
    const seg = emittedRefsSegment(ground.segment_id)(doc);
    expect(seg.placement.from_site_set).toBe(REF);
    expect(seg.apply.scheduling).toEqual(SCHEDULING_PRESETS[ground.scheduling_preset].block);
    const emittedId = identifier(ground.segment_id);
    expect(
      doc.link_rules.some((r: any) =>
        r.endpoints.some((e: any) => selectorNames(e.select).includes(emittedId)),
      ),
    ).toBe(true);
    expect(
      doc.routing.domains.some((d: any) =>
        d.selectors.some((s: any) => selectorNames(s).includes(emittedId)),
      ),
    ).toBe(true);
  });

  it("(serializer parity) matches the save's wrapper form, never the bare site_set", () => {
    // The mutator serializes the applied set through siteSetWrapperFromDraft —
    // the SAME serializer the save stored the snapshot with. A bare (unwrapped)
    // site_set is a different shape, so it must NOT match: were the mutator
    // serializing the applied set in any other form, the real save's wrapper
    // snapshot would never match and the swap would silently never fire.
    const { ws, ground } = convergeableWorkspace();
    const id = identifier(ground.display_name) || identifier(ground.segment_id);
    const bareSnapshot = siteSetObjectFromDraft(ground, id); // no { site_set } wrapper
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(ws));
    act(() => result.current.convergeGroundToRef(ground.segment_id, REF, bareSnapshot));
    expect(result.current.workspace?.ground_refs).toHaveLength(0); // bare form → no match
    act(() =>
      result.current.convergeGroundToRef(ground.segment_id, REF, siteSetWrapperFromDraft(ground)),
    );
    expect(result.current.workspace?.ground_refs).toHaveLength(1); // wrapper form → matches
  });

  it("(X-close) a snapshot that no longer matches the applied set does NOT swap", () => {
    // edit → save → X-close: the buffer is dropped, so the APPLIED set stays
    // pre-edit while the snapshot reflects the edited content. No match → the
    // inline authored set is kept, nothing converges.
    const { ws, ground } = convergeableWorkspace();
    const edited: DraftGroundSet = {
      ...ground,
      members: [
        ...ground.members,
        refGroundMember("nodalarc:sites/ames.yaml", "ames", "Ames", null),
      ],
    };
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(ws));
    act(() =>
      result.current.convergeGroundToRef(ground.segment_id, REF, siteSetWrapperFromDraft(edited)),
    );
    expect(result.current.workspace?.ground_refs).toHaveLength(0);
    expect(result.current.workspace?.ground.some((d) => d.segment_id === ground.segment_id)).toBe(
      true,
    );
  });

  it("(OK-close) applying the buffer first makes the applied set match, and it swaps", () => {
    // save → OK-close: OK commits the buffer (updateGroundDraft) BEFORE the
    // close, so the applied set equals the snapshot and the swap fires. The
    // mutator reads the just-applied set through commit's functional form.
    const { ws, ground } = convergeableWorkspace();
    const edited: DraftGroundSet = {
      ...ground,
      members: [
        ...ground.members,
        refGroundMember("nodalarc:sites/ames.yaml", "ames", "Ames", null),
      ],
    };
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(ws));
    act(() => {
      result.current.updateGroundDraft(ground.segment_id, { members: edited.members });
      result.current.convergeGroundToRef(ground.segment_id, REF, siteSetWrapperFromDraft(edited));
    });
    expect(result.current.workspace?.ground_refs).toHaveLength(1);
    expect(result.current.workspace?.ground).toHaveLength(0);
  });

  it("(guard) a matched snapshot still does NOT swap when a member carries an override", () => {
    // The wrapper snapshot structurally excludes per-member scheduling_override,
    // so it can match while the set is NOT ref-expressible. The guard is a
    // separate gate — the set that a ref cannot express stays inline.
    const { ws, ground } = convergeableWorkspace();
    ground.members[0]!.scheduling_override = "geo-longest-pass";
    // Rebuild the applied set inside the workspace to carry the override.
    const wsWithOverride = { ...ws, ground: [ground] };
    const snapshot = siteSetWrapperFromDraft(ground); // matches (override is invisible to it)
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(wsWithOverride));
    act(() => result.current.convergeGroundToRef(ground.segment_id, REF, snapshot));
    expect(result.current.workspace?.ground_refs).toHaveLength(0);
    expect(result.current.workspace?.ground).toHaveLength(1);
  });
});

describe("addGroundMember — created vs appended (IG-1 create-focus safety)", () => {
  it("creates the first set (created=true) then appends to it (created=false)", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.startNew("gm-test"));
    let first!: { segmentId: string; created: boolean };
    let second!: { segmentId: string; created: boolean };
    act(() => {
      first = result.current.addGroundMember(
        refGroundMember("nodalarc:sites/a.yaml", "a", "A", null),
        () => newDraftGroundSet(GROUND_NODE, {}),
      );
    });
    act(() => {
      second = result.current.addGroundMember(
        refGroundMember("nodalarc:sites/b.yaml", "b", "B", null),
        () => newDraftGroundSet(GROUND_NODE, {}),
      );
    });
    // The first Use created the set (safe to create-focus its name); the second
    // only appended, so its caller must NOT steal focus onto the existing name.
    expect(first.created).toBe(true);
    expect(second.created).toBe(false);
    expect(second.segmentId).toBe(first.segmentId); // same receiving set
    expect(result.current.workspace?.ground).toHaveLength(1);
    expect(result.current.workspace?.ground[0]?.members).toHaveLength(2);
  });
});
