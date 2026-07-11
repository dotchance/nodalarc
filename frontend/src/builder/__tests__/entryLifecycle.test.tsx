// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Workspace undo behavior below the backend visual draft. */
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWorkspace } from "../useWorkspace";
import {
  newDraftConstellation,
  newWorkspace,
} from "./fixtures/workspaceFixtures";

const SPACE_NODE = "nodalarc:nodes/space/x.yaml";

function workspaceWithConstellations(name: string, count: number) {
  const workspace = newWorkspace(name);
  for (let index = 0; index < count; index += 1) {
    workspace.space.push(newDraftConstellation(SPACE_NODE));
  }
  return workspace;
}

describe("— undo trust mechanics", () => {
  it("undo restores the workspace to its state before the last mutation", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(workspaceWithConstellations("undo-restore", 0)));
    act(() => result.current.openWorkspace(workspaceWithConstellations("undo-restore", 1)));
    expect(result.current.workspace?.space).toHaveLength(1);
    act(() => result.current.undo());
    expect(result.current.workspace?.space).toHaveLength(0);
  });

  it("undo past the first recorded state is a no-op — never throws, never corrupts", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(workspaceWithConstellations("undo-floor", 0)));
    act(() => result.current.openWorkspace(workspaceWithConstellations("undo-floor", 1)));
    act(() => result.current.undo());
    act(() => result.current.undo());
    expect(result.current.workspace).toBeNull();
    expect(() => act(() => result.current.undo())).not.toThrow();
    expect(result.current.workspace).toBeNull();
  });

  it("the undo history is bounded — 150 mutations, 100 undos land at 50", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(workspaceWithConstellations("undo-cap", 0)));
    for (let count = 1; count <= 150; count += 1) {
      act(() => result.current.openWorkspace(workspaceWithConstellations("undo-cap", count)));
    }
    expect(result.current.workspace?.space).toHaveLength(150);
    for (let count = 0; count < 100; count += 1) {
      act(() => result.current.undo());
    }
    expect(result.current.workspace?.space).toHaveLength(50);
    for (let count = 0; count < 10; count += 1) {
      act(() => result.current.undo());
    }
    expect(result.current.workspace?.space).toHaveLength(50);
  });
});
