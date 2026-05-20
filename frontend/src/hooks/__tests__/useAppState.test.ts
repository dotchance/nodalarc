// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

vi.mock("../../globe/orbitalTrails", () => ({
  setTrailsVisible: vi.fn(),
}));

const { useAppState } = await import("../useAppState");

function makeInputs(overrides?: Record<string, unknown>) {
  return {
    snapshot: null,
    clearSelection: vi.fn(),
    sessionTransitioning: false,
    switching: false,
    ...overrides,
  };
}

function makeSnapshot(overrides?: Record<string, unknown>) {
  return {
    sim_time: "2026-01-01T00:00:00Z",
    wall_time: "2026-01-01T00:00:00Z",
    schema_version: 1,
    session_id: "run-test-0001",
    nodes: [],
    links: [],
    traced_paths: [],
    active_flows: [],
    recent_events: [],
    network_health: { status: "converged", converging_since_ms: null,
                      unreachable_flows: 0, last_convergence_ms: null },
    routing_stack: "isis",
    constellation_name: "test",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_speed: 1.0,
    stale: false,
    ...overrides,
  };
}

describe("useAppState", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  // --- Session Lifecycle ---

  it("catalog visible before any deploy", () => {
    const { result } = renderHook(() => useAppState(makeInputs()));
    expect(result.current.showCatalog).toBe(true);
    expect(result.current.hasEverDeployed).toBe(false);
  });

  it("catalog hides when session becomes active", () => {
    const { result, rerender } = renderHook(
      (props) => useAppState(props),
      { initialProps: makeInputs() },
    );
    expect(result.current.showCatalog).toBe(true);
    rerender(makeInputs({ snapshot: makeSnapshot({ session_status: "ready" }) }));
    expect(result.current.showCatalog).toBe(false);
    expect(result.current.hasEverDeployed).toBe(true);
  });

  it("transitioning closes CLI drawer", () => {
    const clearSelection = vi.fn();
    const { result, rerender } = renderHook(
      (props) => useAppState(props),
      { initialProps: makeInputs({ clearSelection }) },
    );
    act(() => result.current.setCliDrawerOpen(true));
    expect(result.current.cliDrawerOpen).toBe(true);
    rerender(makeInputs({ sessionTransitioning: true, clearSelection }));
    expect(result.current.cliDrawerOpen).toBe(false);
  });

  it("transitioning clears selection", () => {
    const clearSelection = vi.fn();
    const { rerender } = renderHook(
      (props) => useAppState(props),
      { initialProps: makeInputs({ clearSelection }) },
    );
    rerender(makeInputs({ sessionTransitioning: true, clearSelection }));
    expect(clearSelection).toHaveBeenCalled();
  });

  // --- Display Defaults ---

  it("has correct default toggles", () => {
    const { result } = renderHook(() => useAppState(makeInputs()));
    expect(result.current.viewMode).toBe("globe");
    expect(result.current.colorMode).toBe("area");
    expect(result.current.showIslLinks).toBe(true);
    expect(result.current.showGroundLinks).toBe(true);
    expect(result.current.showTrails).toBe(true);
    expect(result.current.showSatPaths).toBe(false);
    expect(result.current.globeMode).toBe("blue-marble");
  });

  it("reference frame persists to localStorage", () => {
    const { result } = renderHook(() => useAppState(makeInputs()));
    act(() => result.current.toggleReferenceFrame());
    expect(localStorage.getItem("nodalarc.referenceFrame")).toBe("earth-fixed");
  });

  it("reference frame restores from localStorage", () => {
    localStorage.setItem("nodalarc.referenceFrame", "earth-fixed");
    const { result } = renderHook(() => useAppState(makeInputs()));
    expect(result.current.referenceFrame).toBe("earth-fixed");
  });

  // --- State Transitions ---

  it("toggleView cycles globe <-> topology", () => {
    const { result } = renderHook(() => useAppState(makeInputs()));
    expect(result.current.viewMode).toBe("globe");
    act(() => result.current.toggleView());
    expect(result.current.viewMode).toBe("topology");
    act(() => result.current.toggleView());
    expect(result.current.viewMode).toBe("globe");
  });

  it("null snapshot does not crash derived state", () => {
    const { result } = renderHook(() => useAppState(makeInputs({ snapshot: null })));
    expect(result.current.sessionStatus).toBe("idle");
    expect(result.current.hasActiveSession).toBe(false);
    expect(result.current.activeSessionName).toBeNull();
    expect(result.current.simTimeAdvanced).toBe(false);
  });

  it("switching + active session cleans up state", () => {
    const clearSelection = vi.fn();
    const { result, rerender } = renderHook(
      (props) => useAppState(props),
      { initialProps: makeInputs({
          snapshot: makeSnapshot({ session_status: "ready" }),
          clearSelection,
        }),
      },
    );
    act(() => result.current.setCliDrawerOpen(true));
    rerender(makeInputs({ sessionTransitioning: true, switching: true, clearSelection }));
    expect(result.current.cliDrawerOpen).toBe(false);
    expect(clearSelection).toHaveBeenCalled();
  });
});
