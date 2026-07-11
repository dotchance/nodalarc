// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace mutation and undo behavior below the backend visual draft. */
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWorkspace } from "../useWorkspace";
import {
  refGroundMember,
} from "../workspace";
import {
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
} from "./fixtures/workspaceFixtures";

const SPACE_NODE = "nodalarc:nodes/space/x.yaml";
const GROUND_NODE = "nodalarc:nodes/ground/gw.yaml";

describe("addGroundMember — created vs appended (create-focus safety)", () => {
  it("creates the first set (created=true) then appends to it (created=false)", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(newWorkspace("gm-test")));
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

describe("— undo trust mechanics", () => {
  it("undo restores the workspace to its state before the last mutation", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(newWorkspace("undo-restore")));
    act(() => result.current.addConstellation(newDraftConstellation(SPACE_NODE)));
    expect(result.current.workspace?.space).toHaveLength(1);
    act(() => result.current.undo());
    expect(result.current.workspace?.space).toHaveLength(0); // back to the pre-add draft
  });

  it("undo past the first recorded state is a no-op — never throws, never corrupts", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(newWorkspace("undo-floor")));
    act(() => result.current.addConstellation(newDraftConstellation(SPACE_NODE)));
    act(() => result.current.undo()); // → the empty draft
    act(() => result.current.undo()); // → null (the pre-workspace state)
    expect(result.current.workspace).toBeNull();
    // Undoing with an exhausted history is a stable no-op.
    expect(() => act(() => result.current.undo())).not.toThrow();
    expect(result.current.workspace).toBeNull();
  });

  it("the undo history is bounded — 150 mutations, 100 undos land at 50 (the oldest 50 dropped)", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(newWorkspace("undo-cap")));
    for (let i = 0; i < 150; i++) {
      act(() => result.current.addConstellation(newDraftConstellation(SPACE_NODE)));
    }
    expect(result.current.workspace?.space).toHaveLength(150);
    // The bounded history (HISTORY_LIMIT 100) can only walk back 100 states.
    for (let i = 0; i < 100; i++) act(() => result.current.undo());
    expect(result.current.workspace?.space).toHaveLength(50); // not 0 — the oldest 50 were capped off
    // Beyond the cap floor, further undos are no-ops.
    for (let i = 0; i < 10; i++) act(() => result.current.undo());
    expect(result.current.workspace?.space).toHaveLength(50);
  });
});
