// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSelection } from "../useSelection";

describe("useSelection", () => {
  it("selects a satellite", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.select({ type: "satellite", id: "sat-P00S00" }));
    expect(result.current.selection).toEqual({ type: "satellite", id: "sat-P00S00" });
  });

  it("selects a link", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.select({ type: "link", id: "sat-P00S00:sat-P00S01" }));
    expect(result.current.selection?.type).toBe("link");
  });

  it("select replaces previous selection", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.select({ type: "satellite", id: "a" }));
    act(() => result.current.select({ type: "ground_station", id: "b" }));
    expect(result.current.selection?.id).toBe("b");
    expect(result.current.selection?.type).toBe("ground_station");
  });

  it("clearSelection sets null", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.select({ type: "satellite", id: "a" }));
    act(() => result.current.clearSelection());
    expect(result.current.selection).toBeNull();
  });

  it("clearSelection is idempotent", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.clearSelection());
    act(() => result.current.clearSelection());
    expect(result.current.selection).toBeNull();
  });
});
