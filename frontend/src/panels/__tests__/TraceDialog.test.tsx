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

function snapshotWithActiveTrace(): StateSnapshot {
  const traced: TracedPath = {
    flow_id: "__continuous_trace__",
    src_node: "madrid-gw",
    dst_node: "luna-gw",
    hops: ["madrid-gw", "leo-1", "geo-1", "luna-gw"],
    reverse_hops: ["luna-gw", "geo-1", "leo-1", "madrid-gw"],
    rtt_ms: 560,
    reverse_rtt_ms: 2862,
    method: "tracepath",
  };
  return { traced_paths: [traced] } as unknown as StateSnapshot;
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
