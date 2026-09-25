// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GroundStationDetail } from "../GroundStationDetail";
import type { NodeRole, NodeRoutingInstance, NodeState, StateSnapshot } from "../../types";

function station(role: NodeRole, instances: NodeRoutingInstance[]): NodeState {
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
    routing_instances: instances,
    role,
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
  return { nodes: [node], links: [], traced_paths: [] } as unknown as StateSnapshot;
}

const ISIS: NodeRoutingInstance = {
  domain_id: "orbital",
  protocol: "isis",
  areas: ["49.0001"],
  interfaces: [{ name: "term0", area_id: null }],
  area_border: false,
  as_boundary: false,
};

const OSPF_ABR: NodeRoutingInstance = {
  domain_id: "terrestrial",
  protocol: "ospf",
  areas: ["0.0.0.0", "0.0.0.1"],
  interfaces: [
    { name: "terr0", area_id: "0.0.0.0" },
    { name: "terr1", area_id: "0.0.0.1" },
  ],
  area_border: true,
  as_boundary: true,
};

describe("GroundStationDetail", () => {
  beforeEach(() => {
    // The decision panels fetch on mount; this test reads only the node facts.
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("shows the role and each instance's areas the backend resolved", () => {
    const node = station("router", [ISIS, OSPF_ABR]);
    render(<GroundStationDetail node={node} snapshot={snapshot(node)} onSelect={vi.fn()} />);

    expect(screen.getByTestId("routing-role").textContent).toBe("Router");
    expect(screen.getByText("IS-IS orbital")).toBeTruthy();
    expect(screen.getByTestId("routing-instance-orbital").textContent).toBe("area 49.0001");
    expect(screen.getByText("OSPF terrestrial")).toBeTruthy();
    expect(screen.getByTestId("routing-instance-terrestrial").textContent).toBe(
      "areas 0.0.0.0, 0.0.0.1 · ABR · ASBR",
    );
    expect(screen.queryByText("Gateway")).toBeNull();
  });

  it("says so for a host in no routing instance", () => {
    const node = station("host", []);
    render(<GroundStationDetail node={node} snapshot={snapshot(node)} onSelect={vi.fn()} />);

    expect(screen.getByTestId("routing-role").textContent).toBe("Host");
    expect(screen.getByText("no routing instance")).toBeTruthy();
  });
});
