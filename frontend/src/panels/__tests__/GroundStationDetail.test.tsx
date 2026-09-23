// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GroundStationDetail } from "../GroundStationDetail";
import type { NodeState, StateSnapshot } from "../../types";

function station(routingArea: string | null): NodeState {
  return {
    node_id: "earth-de-frankfurt-gw1",
    node_type: "ground_station",
    lat_deg: 50.1,
    lon_deg: 8.7,
    alt_km: 0.1,
    vel_x_km_s: null,
    vel_y_km_s: null,
    vel_z_km_s: null,
    plane: null,
    slot: null,
    routing_area: routingArea,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    min_elevation_deg: 10,
    beam_falloff_exponent: null,
    reference_body: "earth",
    frame_id: "earth_fixed",
  };
}

function snapshot(node: NodeState): StateSnapshot {
  return { nodes: [node], links: [], active_flows: [], traced_paths: [] } as unknown as StateSnapshot;
}

function routingAreaValue(): string | null {
  return screen.getByText("Routing Area").nextElementSibling?.textContent ?? null;
}

describe("GroundStationDetail", () => {
  beforeEach(() => {
    // The decision panels fetch on mount; this test reads only the node facts.
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("shows the routing area VS-API resolved for the station", () => {
    const node = station("49.0001");
    render(<GroundStationDetail node={node} snapshot={snapshot(node)} onSelect={vi.fn()} />);

    expect(routingAreaValue()).toBe("49.0001");
    expect(screen.queryByText("Gateway")).toBeNull();
    expect(screen.queryByText("ground")).toBeNull();
  });

  it("says none for a station that runs no area protocol", () => {
    const node = station(null);
    render(<GroundStationDetail node={node} snapshot={snapshot(node)} onSelect={vi.fn()} />);

    expect(routingAreaValue()).toBe("none");
  });
});
