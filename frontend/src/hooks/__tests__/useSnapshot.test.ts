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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
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

  it("ignores stale historical responses from an older request", async () => {
    const oldHistorical = makeSnapshot({
      sim_time: "2026-01-01T00:01:00Z",
      nodes: [
        { node_id: "sat-old", node_type: "satellite" } as StateSnapshot["nodes"][number],
      ],
    });
    const newHistorical = makeSnapshot({
      sim_time: "2026-01-01T00:02:00Z",
      nodes: [
        { node_id: "sat-new", node_type: "satellite" } as StateSnapshot["nodes"][number],
      ],
    });
    const oldResponse = deferred<Response>();
    const newResponse = deferred<Response>();
    vi.mocked(globalThis.fetch)
      .mockReturnValueOnce(oldResponse.promise)
      .mockReturnValueOnce(newResponse.promise);
    const { result } = renderHook(() => useSnapshot());
    act(() => result.current.setHistoricalMode(true));

    let oldDone!: Promise<boolean>;
    let newDone!: Promise<boolean>;
    await act(async () => {
      oldDone = result.current.fetchHistorical("2026-01-01T00:01:00Z");
      newDone = result.current.fetchHistorical("2026-01-01T00:02:00Z");
    });

    const firstInit = vi.mocked(globalThis.fetch).mock.calls[0]?.[1] as RequestInit | undefined;
    expect(firstInit?.signal).toBeInstanceOf(AbortSignal);
    expect((firstInit?.signal as AbortSignal).aborted).toBe(true);

    await act(async () => {
      newResponse.resolve({ ok: true, json: async () => newHistorical } as Response);
      expect(await newDone).toBe(true);
    });
    expect(result.current.snapshot?.sim_time).toBe("2026-01-01T00:02:00Z");
    expect(result.current.snapshot?.nodes[0]?.node_id).toBe("sat-new");

    await act(async () => {
      oldResponse.resolve({ ok: true, json: async () => oldHistorical } as Response);
      expect(await oldDone).toBe(false);
    });
    expect(result.current.snapshot?.sim_time).toBe("2026-01-01T00:02:00Z");
    expect(result.current.snapshot?.nodes[0]?.node_id).toBe("sat-new");
  });

});
