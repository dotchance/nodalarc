// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StateSnapshot } from "../../types";

function makeSnapshot(overrides: Partial<StateSnapshot> = {}): StateSnapshot {
  return {
    sim_time: "2026-01-01T00:00:00Z",
    wall_time: "2026-01-01T00:00:00Z",
    schema_version: 1,
    session_id: "run-test-0001",
    nodes: [{ node_id: "sat-1", node_type: "satellite" } as StateSnapshot["nodes"][number]],
    links: [],
    traced_paths: [],
    active_flows: [],
    recent_events: [],
    debug_events: [],
    debug_sources: [],
    network_health: {
      status: "converged",
      converging_since_ms: null,
      unreachable_flows: 0,
      last_convergence_ms: null,
    },
    routing_stack: "isis",
    constellation_name: "test",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_speed: 1.0,
    stale: false,
    ...overrides,
  } as StateSnapshot;
}

const liveSnapshot = makeSnapshot();
let wsState: ReturnType<typeof makeWsState>;

function makeWsState(overrides: Record<string, unknown> = {}) {
  return {
    snapshot: liveSnapshot,
    ephemeris: null,
    playbackState: null,
    connected: true,
    hasEverConnected: true,
    kicked: false,
    sessionTransitioning: false,
    sessionError: null,
    switchDetail: null,
    sendMessage: vi.fn(),
    ...overrides,
  };
}

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: () => ({ Authorization: "Bearer test" }),
}));

vi.mock("../useWebSocket", () => ({
  useWebSocket: () => wsState,
}));

const { useSnapshot } = await import("../useSnapshot");

describe("useSnapshot historical mode", () => {
  beforeEach(() => {
    wsState = makeWsState();
    globalThis.fetch = vi.fn();
  });

  it("seeds historical mode from the current live snapshot instead of blanking the constellation", () => {
    const { result } = renderHook(() => useSnapshot());

    expect(result.current.snapshot?.nodes).toHaveLength(1);
    act(() => result.current.setHistoricalMode(true));

    expect(result.current.historicalMode).toBe(true);
    expect(result.current.snapshot?.nodes).toHaveLength(1);
    expect(result.current.snapshot?.session_id).toBe("run-test-0001");
  });

  it("does not replace the historical view with a non-snapshot REST response", async () => {
    const { result } = renderHook(() => useSnapshot());
    act(() => result.current.setHistoricalMode(true));

    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ error: "No database configured" }),
    } as Response);

    await act(async () => {
      await result.current.fetchHistorical("2026-01-01T00:00:30Z");
    });

    expect(result.current.snapshot?.nodes).toHaveLength(1);
    expect(result.current.snapshot?.session_id).toBe("run-test-0001");
  });

  it("replaces the historical view with a valid historical snapshot", async () => {
    const historical = makeSnapshot({
      sim_time: "2026-01-01T00:01:00Z",
      session_id: "run-test-0001",
      nodes: [
        { node_id: "sat-1", node_type: "satellite" } as StateSnapshot["nodes"][number],
        { node_id: "sat-2", node_type: "satellite" } as StateSnapshot["nodes"][number],
      ],
    });
    const { result } = renderHook(() => useSnapshot());
    act(() => result.current.setHistoricalMode(true));

    vi.mocked(globalThis.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => historical,
    } as Response);

    await act(async () => {
      await result.current.fetchHistorical("2026-01-01T00:01:00Z");
    });

    expect(result.current.snapshot?.sim_time).toBe("2026-01-01T00:01:00Z");
    expect(result.current.snapshot?.nodes).toHaveLength(2);
  });
});
