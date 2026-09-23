// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TraceDialog } from "../TraceDialog";
import type { NodeState, StateSnapshot, TracedPath } from "../../types";

afterEach(cleanup);

function node(node_id: string): NodeState {
  return {
    node_id,
    node_type: node_id.includes("gw") ? "ground_station" : "satellite",
    lat_deg: 0,
    lon_deg: 0,
    alt_km: 550,
    vel_x_km_s: 0,
    vel_y_km_s: 0,
    vel_z_km_s: 0,
    plane: null,
    slot: null,
    routing_area: null,
  } as NodeState;
}

function reachedTrace(): TracedPath {
  return {
    flow_id: "__continuous_trace__",
    src_node: "madrid-gw",
    dst_node: "luna-gw",
    hops: ["madrid-gw", "leo-1", "geo-1", "luna-gw"],
    hop_rtts: [null, 5, 250, 560],
    state: "reached",
    rtt_ms: 560,
    error: null,
    reverse_hops: ["luna-gw", "geo-1", "leo-1", "madrid-gw"],
    reverse_hop_rtts: [null, 2300, 2800, 2862],
    reverse_state: "reached",
    reverse_rtt_ms: 2862,
    reverse_error: null,
    asymmetry_detected: false,
    tracing: true,
    traced_at: "2026-09-23T00:00:00Z",
    sim_time: "2026-09-23T00:00:00Z",
  };
}

function snapshotWith(traced: TracedPath): StateSnapshot {
  return { traced_paths: [traced] } as unknown as StateSnapshot;
}

function snapshotWithActiveTrace(): StateSnapshot {
  return snapshotWith(reachedTrace());
}

const NODES = [node("madrid-gw"), node("luna-gw")];

describe("TraceDialog stop control", () => {
  it("shows Stop for a server-side trace even when the dialog mounts fresh", () => {
    // The dialog was never told (locally) that a trace started — it opened
    // fresh after the user navigated away and back. The active trace lives in
    // the snapshot, so Stop must appear anyway.
    render(<TraceDialog nodes={NODES} snapshot={snapshotWithActiveTrace()} />);
    expect(screen.getByText("Stop Trace")).toBeTruthy();
    expect(screen.queryByText("Trace")).toBeNull();
  });

  it("posts to /trace/stop when Stop is clicked", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<TraceDialog nodes={NODES} snapshot={snapshotWithActiveTrace()} />);
      fireEvent.click(screen.getByText("Stop Trace"));
      await Promise.resolve();
      const called = fetchMock.mock.calls.some(
        ([url, opts]) => String(url).endsWith("/api/v1/trace/stop") && opts?.method === "POST",
      );
      expect(called).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows Trace (not Stop) when no server trace is active", () => {
    render(
      <TraceDialog nodes={NODES} snapshot={{ traced_paths: [] } as unknown as StateSnapshot} />,
    );
    expect(screen.getByText("Trace")).toBeTruthy();
    expect(screen.queryByText("Stop Trace")).toBeNull();
  });
});

describe("TraceDialog outcomes", () => {
  it("shows round trips and no outcome line when both directions reached", () => {
    render(<TraceDialog nodes={NODES} snapshot={snapshotWithActiveTrace()} />);
    expect(screen.getByText(/560\.0ms fwd \/ 2862\.0ms rev/)).toBeTruthy();
    // Per-hop delays: the first hop from the source, then consecutive deltas.
    expect(screen.getByText("5.0ms")).toBeTruthy();
    expect(screen.getByText("245.0ms")).toBeTruthy();
    expect(screen.getByText("310.0ms")).toBeTruthy();
    expect(screen.getByText("LIVE")).toBeTruthy();
    expect(screen.queryByText(/did not answer|could not run|asymmetry/)).toBeNull();
  });

  it("says which direction failed and why, with no round trip", () => {
    const traced: TracedPath = {
      ...reachedTrace(),
      hops: ["madrid-gw"],
      hop_rtts: [null],
      state: "failed",
      rtt_ms: null,
      error: "madrid-gw: expected one live session pod, found 0",
      asymmetry_detected: null,
    };
    render(<TraceDialog nodes={NODES} snapshot={snapshotWith(traced)} />);
    expect(
      screen.getByText("Forward trace could not run: madrid-gw: expected one live session pod, found 0"),
    ).toBeTruthy();
    expect(screen.queryByText(/ms fwd/)).toBeNull();
  });

  it("says the destination did not answer, counting the hops that ran", () => {
    const traced: TracedPath = {
      ...reachedTrace(),
      hops: ["madrid-gw", "leo-1", "*", "*"],
      hop_rtts: [null, 5, null, null],
      state: "not_reached",
      rtt_ms: null,
      asymmetry_detected: null,
    };
    render(<TraceDialog nodes={NODES} snapshot={snapshotWith(traced)} />);
    expect(screen.getByText("Forward: destination did not answer after 3 hops")).toBeTruthy();
    expect(screen.getAllByText("*")).toHaveLength(2);
  });

  it("marks a trace whose loop stopped", () => {
    render(<TraceDialog nodes={NODES} snapshot={snapshotWith({ ...reachedTrace(), tracing: false })} />);
    expect(screen.getByText("STOPPED")).toBeTruthy();
    expect(screen.queryByText("LIVE")).toBeNull();
  });

  it("reports asymmetry only when the trace measured it", () => {
    render(
      <TraceDialog nodes={NODES} snapshot={snapshotWith({ ...reachedTrace(), asymmetry_detected: true })} />,
    );
    expect(screen.getByText("Path asymmetry detected")).toBeTruthy();
  });
});
