// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** useSessionImport: the session entry/import state machine. Pins all five
 *  members against the REAL useWorkspace (so displace actually stashes and
 *  openWorkspace actually adopts): the running-session auto-import guard; the
 *  stash-before-adopt ordering (the displaced draft lands in the backup slot,
 *  and the imported session's autosave never overwrites it); the in-flight race
 *  (a user-started workspace wins); the in-flight termination (a load ending
 *  without its document clears importPending so Open re-enables); the
 *  refused-import read-only path.
 *
 *  The harness owns `loading`/`loaded` so loadSession marks the load in flight
 *  exactly as the real fetch does — otherwise the termination effect would fire
 *  the instant importPending is set.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRef, useState } from "react";
import { useSessionImport } from "../useSessionImport";
import { useWorkspace, serializeWorkspace } from "../useWorkspace";
import { newWorkspace, newDraftConstellation, toSessionDocument } from "../workspace";
import type { BuilderSessionListEntry } from "../builderTypes";

const AUTOSAVE_KEY = "nodalarc-builder-draft";
const BACKUP_KEY = "nodalarc-builder-draft-previous";
const SPACE_NODE = "nodalarc:nodes/space/x.yaml";

const entry = (file: string, name = file): BuilderSessionListEntry =>
  ({ file, name, source: "nodalarc", active: true, constellation: "x" }) as BuilderSessionListEntry;

/** A session document workspaceFromSessionDocument accepts. */
function validDoc(name: string): Record<string, unknown> {
  const ws = newWorkspace(name);
  ws.space.push(newDraftConstellation(SPACE_NODE));
  return toSessionDocument(ws);
}

function useHarness({
  active,
  running,
}: {
  active: boolean;
  running: BuilderSessionListEntry | null;
}) {
  const ws = useWorkspace();
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState<{ doc: Record<string, unknown>; file: string } | null>(null);
  const loadCalls = useRef<string[]>([]);
  const displace = (proceed: () => void) => {
    // Mirrors BuilderView: stash the current draft to the backup slot first; a
    // refused stash holds the gesture (the choice dialog owns it).
    if (ws.stashAutosaveToBackup() === "refused") return;
    proceed();
  };
  const si = useSessionImport({
    active,
    workspace: ws.workspace,
    runningSession: running,
    loadedDocument: loaded?.doc ?? null,
    loadedFile: loaded?.file ?? null,
    loading,
    loadSession: (f) => {
      loadCalls.current.push(f);
      setLoading(true); // in flight, exactly as the real fetch does
    },
    displace,
    openWorkspace: ws.openWorkspace,
  });
  return {
    ws,
    si,
    loadCalls,
    resolve: (doc: Record<string, unknown>, file: string) => {
      setLoaded({ doc, file });
      setLoading(false);
    },
    fail: () => {
      setLoaded(null);
      setLoading(false);
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe("useSessionImport", () => {
  it("(1) auto-import guard: fires on entry beside a running session; blocked when inactive or workspace-present", () => {
    // Inactive: never a background importer.
    const inactive = renderHook((p) => useHarness(p), {
      initialProps: { active: false, running: entry("guard-inactive.yaml") },
    });
    expect(inactive.result.current.si.importPending).toBeNull();
    expect(inactive.result.current.loadCalls.current).toEqual([]);

    // Active + running + no workspace: the running session loads.
    const fires = renderHook((p) => useHarness(p), {
      initialProps: { active: true, running: entry("guard-fires.yaml") },
    });
    expect(fires.result.current.si.importPending?.file).toBe("guard-fires.yaml");
    expect(fires.result.current.loadCalls.current).toEqual(["guard-fires.yaml"]);

    // Workspace present: auto-import must not displace the live draft.
    const withWs = renderHook((p) => useHarness(p), {
      initialProps: { active: false, running: entry("guard-ws.yaml") },
    });
    act(() => withWs.result.current.ws.startNew("mine"));
    withWs.rerender({ active: true, running: entry("guard-ws.yaml") });
    expect(withWs.result.current.si.importPending).toBeNull();
    expect(withWs.result.current.loadCalls.current).toEqual([]);
  });

  it("(2) stash ordering: the displaced draft lands in the backup, and the import's autosave does not overwrite it", () => {
    // A pre-import draft sits in the autosave slot.
    const priorDraft = newWorkspace("prior-draft");
    priorDraft.space.push(newDraftConstellation(SPACE_NODE));
    localStorage.setItem(AUTOSAVE_KEY, serializeWorkspace(priorDraft));

    const { result } = renderHook((p) => useHarness(p), {
      initialProps: { active: true, running: null },
    });
    act(() => result.current.si.startImport(entry("stash.yaml")));
    act(() => result.current.resolve(validDoc("imported-session"), "stash.yaml"));

    // The displaced draft was stashed to the backup BEFORE adoption.
    expect(JSON.parse(localStorage.getItem(BACKUP_KEY)!).workspace.name).toBe("prior-draft");
    // The imported session is adopted, with its provenance recorded.
    expect(result.current.ws.workspace?.name).toBe("imported-session");
    expect(result.current.si.importedFrom).toBe("stash.yaml");

    // The imported session's autosave writes AUTOSAVE_KEY, never the backup.
    act(() => vi.advanceTimersByTime(1000));
    expect(JSON.parse(localStorage.getItem(BACKUP_KEY)!).workspace.name).toBe("prior-draft");
  });

  it("(3) in-flight race: a workspace the user started while the load was pending wins", () => {
    const { result } = renderHook((p) => useHarness(p), {
      initialProps: { active: true, running: null },
    });
    act(() => result.current.si.startImport(entry("race.yaml")));
    // The user starts their own workspace before the load lands.
    act(() => result.current.ws.startNew("mine"));
    act(() => result.current.resolve(validDoc("would-be-imported"), "race.yaml"));

    expect(result.current.ws.workspace?.name).toBe("mine"); // theirs wins
    expect(result.current.si.importPending).toBeNull();
    expect(result.current.si.importedFrom).toBeNull(); // never adopted the import
  });

  it("(4) in-flight termination: a load ending without its document clears importPending (Open re-enables)", () => {
    const { result } = renderHook((p) => useHarness(p), {
      initialProps: { active: true, running: null },
    });
    act(() => result.current.si.startImport(entry("term.yaml")));
    expect(result.current.si.importPending?.file).toBe("term.yaml"); // in flight
    // The load ends without a document (failed fetch / competing clear).
    act(() => result.current.fail());
    expect(result.current.si.importPending).toBeNull();
  });

  it("(5) refused import: an unrepresentable session sets importIssues and adopts no workspace", () => {
    const { result } = renderHook((p) => useHarness(p), {
      initialProps: { active: true, running: null },
    });
    act(() => result.current.si.startImport(entry("refused.yaml")));
    act(() => result.current.resolve({ mystery_top_level_key: {} }, "refused.yaml"));

    expect(result.current.si.importIssues?.name).toBe("refused.yaml");
    expect(result.current.si.importIssues?.issues.length).toBeGreaterThan(0);
    expect(result.current.ws.workspace).toBeNull(); // nothing adopted
    expect(result.current.si.importPending).toBeNull();
  });
});
