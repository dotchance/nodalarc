// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** builderSnapshotFromWorld — the resolved-world → Scene-input derivation.
 *
 *  Pins: satellite positions come from epoch propagation with the shared
 *  client math; ground positions come from the resolver surface_position
 *  (including nodes the ephemeris omits — the world is the node universe);
 *  the snapshot is frozen at the session epoch.
 */

import { describe, expect, it } from "vitest";
import { builderSnapshotFromWorld } from "../builderSnapshot";
import type { BuilderWorld } from "../builderTypes";
import type { SessionEphemeris } from "../../sim/ephemeris";

const EPOCH_ISO = "2026-06-08T00:00:00+00:00";
const EPOCH_UNIX = Date.parse(EPOCH_ISO) / 1000;

const EARTH_FRAME = {
  body_id: "earth",
  mean_radius_km: 6371.0088,
  equatorial_radius_km: 6378.137,
  polar_radius_km: 6356.7523,
  gravitational_parameter_km3_s2: 398600.4418,
  rotation_rate_rad_s: 7.2921159e-5,
  j2: 0.00108263,
  origin_x_km: 0,
  origin_y_km: 0,
  origin_z_km: 0,
  vel_x_km_s: 0,
  vel_y_km_s: 0,
  vel_z_km_s: 0,
  provider: "analytic",
  kernel_id: "none",
  quality_tier: "test",
  frame: "eci",
};

const EPHEMERIS: SessionEphemeris = {
  epoch_id: 0,
  sim_time: EPOCH_ISO,
  epoch_unix: EPOCH_UNIX,
  nodes: {
    "leo-sat-p00s00": {
      type: "keplerian",
      propagator: "two-body",
      semi_major_axis_km: 6928.137,
      eccentricity: 0,
      inclination_deg: 53,
      raan_deg: 0,
      argument_of_perigee_deg: 0,
      mean_anomaly_deg: 0,
      plane: 0,
      slot: 0,
      segment_id: "leo",
      reference_body: "earth",
      frame_id: "earth",
    },
    "ground-gw1": {
      type: "fixed",
      lat_deg: 39.7392,
      lon_deg: -104.9903,
      alt_km: 1.609,
      segment_id: "ground",
      reference_body: "earth",
      frame_id: "earth",
    },
  },
  body_frames: { earth: EARTH_FRAME },
};

const WORLD: BuilderWorld = {
  session: { name: "test-session", display_name: null, description: null },
  epoch_unix: EPOCH_UNIX,
  ephemeris: EPHEMERIS,
  nodes: [
    {
      node_id: "leo-sat-p00s00",
      local_node_id: "sat-p00s00",
      segment_id: "leo",
      namespace: "leo",
      kind: "satellite",
      plane: 0,
      slot: 0,
      tags: [],
      surface_position: null,
    },
    {
      node_id: "ground-gw1",
      local_node_id: "gw1",
      segment_id: "ground",
      namespace: "ground",
      kind: "ground_station",
      plane: null,
      slot: null,
      tags: ["leo"],
      surface_position: { body: "earth", lat_deg: 39.7392, lon_deg: -104.9903, alt_m: 1609 },
    },
    {
      // A ground node with no space links: absent from the ephemeris, still
      // in the world (the denver-gw2 case) — must render from surface_position.
      node_id: "ground-gw2",
      local_node_id: "gw2",
      segment_id: "ground",
      namespace: "ground",
      kind: "ground_station",
      plane: null,
      slot: null,
      tags: ["meo"],
      surface_position: { body: "earth", lat_deg: 39.7392, lon_deg: -104.9903, alt_m: 1609 },
    },
  ],
};

describe("builderSnapshotFromWorld", () => {
  it("derives one NodeState per world node, including ephemeris-absent grounds", () => {
    const snapshot = builderSnapshotFromWorld(WORLD);
    expect(snapshot.nodes.map((n) => n.node_id).sort()).toEqual([
      "ground-gw1",
      "ground-gw2",
      "leo-sat-p00s00",
    ]);
  });

  it("propagates satellites at the session epoch", () => {
    const snapshot = builderSnapshotFromWorld(WORLD);
    const sat = snapshot.nodes.find((n) => n.node_id === "leo-sat-p00s00")!;
    expect(sat.node_type).toBe("satellite");
    // 550 km circular orbit: geodetic altitude within the ellipsoid spread.
    expect(sat.alt_km).toBeGreaterThan(500);
    expect(sat.alt_km).toBeLessThan(600);
    expect(Number.isFinite(sat.lat_deg)).toBe(true);
    expect(Number.isFinite(sat.lon_deg)).toBe(true);
  });

  it("places grounds from the resolver surface_position", () => {
    const snapshot = builderSnapshotFromWorld(WORLD);
    const gw2 = snapshot.nodes.find((n) => n.node_id === "ground-gw2")!;
    expect(gw2.lat_deg).toBeCloseTo(39.7392);
    expect(gw2.lon_deg).toBeCloseTo(-104.9903);
    expect(gw2.alt_km).toBeCloseTo(1.609);
    expect(gw2.reference_body).toBe("earth");
  });

  it("freezes the snapshot at the epoch with no links", () => {
    const snapshot = builderSnapshotFromWorld(WORLD);
    expect(snapshot.sim_time).toBe(EPOCH_ISO);
    expect(snapshot.playback_paused).toBe(true);
    expect(snapshot.links).toEqual([]);
  });

  it("fails loudly on a satellite without an ephemeris entry", () => {
    const broken: BuilderWorld = {
      ...WORLD,
      nodes: WORLD.nodes.map((node) =>
        node.kind === "satellite" ? { ...node, node_id: "leo-sat-missing" } : node,
      ),
    };
    expect(() => builderSnapshotFromWorld(broken)).toThrow(/no ephemeris entry/);
  });
});
