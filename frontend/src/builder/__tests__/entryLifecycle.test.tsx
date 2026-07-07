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
import { EARTH_BODY_REF, type Workspace } from "../workspace";

const AUTOSAVE_KEY = "nodalarc-builder-draft";
const BACKUP_KEY = "nodalarc-builder-draft-previous";
const SPACE_NODE = "nodalarc:nodes/space/x.yaml";

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
