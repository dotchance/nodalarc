// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Candidate preview — LOS math and rule-scoped pair generation.
 *
 *  Pins: occlusion (a far-side ground never pairs), min-elevation gating,
 *  zero-candidate honesty notes, and the physical-space forward transform.
 */

import { describe, expect, it } from "vitest";
import { computeCandidates } from "../candidates";
import {
  elevationDeg,
  geodeticToBodyFixedKm,
  segmentIntersectsBody,
} from "../../sim/lineOfSight";
import type { BuilderLinkRule, BuilderWorld, BuilderWorldNode } from "../builderTypes";
import { propagateNode, type SessionEphemeris } from "../../sim/ephemeris";

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

const BODY_MATH = {
  bodyId: "earth",
  meanRadiusKm: EARTH_FRAME.mean_radius_km,
  equatorialRadiusKm: EARTH_FRAME.equatorial_radius_km,
  polarRadiusKm: EARTH_FRAME.polar_radius_km,
  gravitationalParameterKm3S2: EARTH_FRAME.gravitational_parameter_km3_s2,
  rotationRateRadS: EARTH_FRAME.rotation_rate_rad_s,
  j2: EARTH_FRAME.j2,
  kmPerRenderUnit: 1,
};

function accessBlock(role: string): BuilderWorldNode["terminal_inventory"][number] {
  return {
    terminal_id: `${role}_0`,
    owner_node_id: "n",
    endpoint_role: role,
    medium: "rf",
    source_terminal_id: null,
    link_role: null,
    count: 4,
    tracking_capacity: null,
    max_range_km: null,
    min_elevation_deg: null,
    field_of_regard_deg: null,
    tracking_rate_deg_s: null,
    bandwidth_mbps: null,
    source_ref: "x",
  };
}

function groundNode(id: string, latDeg: number, lonDeg: number): BuilderWorldNode {
  return {
    node_id: id,
    local_node_id: id,
    segment_id: "ground",
    namespace: "ground",
    kind: "ground_station",
    plane: null,
    slot: null,
    tags: [],
    surface_position: { body: "earth", lat_deg: latDeg, lon_deg: lonDeg, alt_m: 0 },
    forwarding: "routed",
    terminal_inventory: [accessBlock("access")],
    interfaces: null,
    originated_prefixes: null,
  };
}

// One satellite directly over (0, 0) at 550 km; grounds at the subsatellite
// point and at the antipode.
const EPHEMERIS: SessionEphemeris = {
  epoch_id: 0,
  sim_time: EPOCH_ISO,
  epoch_unix: EPOCH_UNIX,
  nodes: {
    "leo-sat": {
      type: "keplerian",
      propagator: "two-body",
      semi_major_axis_km: 6928.137,
      eccentricity: 0,
      inclination_deg: 0,
      raan_deg: 0,
      argument_of_perigee_deg: 0,
      mean_anomaly_deg: 0,
      plane: 0,
      slot: 0,
      segment_id: "leo",
      reference_body: "earth",
      frame_id: "earth",
    },
  },
  body_frames: { earth: EARTH_FRAME },
};

function world(rules: BuilderLinkRule[]): BuilderWorld {
  // Place the near ground at the satellite's actual epoch ground point: the
  // two-body propagation at the epoch puts the satellite over (0, lonSat).
  return {
    session: { name: "t", display_name: null, description: null },
    epoch_unix: EPOCH_UNIX,
    ephemeris: EPHEMERIS,
    nodes: [
      {
        node_id: "leo-sat",
        local_node_id: "sat",
        segment_id: "leo",
        namespace: "leo",
        kind: "satellite",
        plane: 0,
        slot: 0,
        tags: [],
        surface_position: null,
        forwarding: "routed",
        terminal_inventory: [accessBlock("access"), accessBlock("isl")],
        interfaces: null,
        originated_prefixes: null,
      },
      groundNode("ground-near", 0, SAT_LON_DEG),
      groundNode("ground-far", 0, SAT_LON_DEG > 0 ? SAT_LON_DEG - 180 : SAT_LON_DEG + 180),
    ],
    link_rules: rules,
  };
}

// Derive the satellite's epoch longitude with the same propagation the
// preview uses, so the "near" ground truly sits underneath it.
const satAtEpoch = propagateNode(
  EPHEMERIS.nodes["leo-sat"]!,
  EPOCH_UNIX,
  EPOCH_UNIX,
  BODY_MATH,
);
const SAT_LON_DEG = satAtEpoch.lonDeg;

const ACCESS_RULE: BuilderLinkRule = {
  rule_id: "access",
  kind: "access",
  enabled: true,
  endpoints: [
    {
      segment_id: "ground",
      terminal_role: "access",
      terminal_medium: "rf",
      min_elevation_deg: 5,
      node_ids: ["ground-near", "ground-far"],
    },
    {
      segment_id: "leo",
      terminal_role: "access",
      terminal_medium: "rf",
      min_elevation_deg: null,
      node_ids: ["leo-sat"],
    },
  ],
  topology_mode: "visible_candidates",
  topology_n: null,
  explicit_pairs: [],
  max_range_km: null,
};

describe("lineOfSight primitives", () => {
  it("forward transform lands on the ellipsoid surface", () => {
    const p = geodeticToBodyFixedKm(0, 0, 0, BODY_MATH);
    expect(p[0]).toBeCloseTo(BODY_MATH.equatorialRadiusKm, 3);
    const pole = geodeticToBodyFixedKm(90, 0, 0, BODY_MATH);
    expect(pole[2]).toBeCloseTo(BODY_MATH.polarRadiusKm, 3);
  });

  it("elevation is 90 at zenith and negative below the horizon", () => {
    const site = geodeticToBodyFixedKm(0, 0, 0, BODY_MATH);
    const overhead = geodeticToBodyFixedKm(0, 0, 550, BODY_MATH);
    expect(elevationDeg(0, 0, site, overhead)).toBeCloseTo(90, 5);
    const behind = geodeticToBodyFixedKm(0, 180, 550, BODY_MATH);
    expect(elevationDeg(0, 0, site, behind)).toBeLessThan(0);
  });

  it("occlusion blocks chords through the body and passes surface grazes", () => {
    const a = geodeticToBodyFixedKm(0, 0, 550, BODY_MATH);
    const b = geodeticToBodyFixedKm(0, 180, 550, BODY_MATH);
    expect(segmentIntersectsBody(a, b, BODY_MATH.meanRadiusKm)).toBe(true);
    const c = geodeticToBodyFixedKm(0, 10, 550, BODY_MATH);
    expect(segmentIntersectsBody(a, c, BODY_MATH.meanRadiusKm)).toBe(false);
  });
});

describe("computeCandidates", () => {
  it("pairs the visible ground and excludes the far-side ground", () => {
    const { pairs, previews } = computeCandidates(world([ACCESS_RULE]));
    expect(pairs.map((p) => `${p.a}~${p.b}`)).toEqual(["ground-near~leo-sat"]);
    expect(previews[0]!.candidates).toBe(1);
    expect(previews[0]!.note).toBeNull();
  });

  it("reports the honesty note when geometry forbids everything", () => {
    const darkRule: BuilderLinkRule = {
      ...ACCESS_RULE,
      rule_id: "dark",
      endpoints: [
        { ...ACCESS_RULE.endpoints[0], node_ids: ["ground-far"] },
        ACCESS_RULE.endpoints[1],
      ],
    };
    const { pairs, previews } = computeCandidates(world([darkRule]));
    expect(pairs).toEqual([]);
    expect(previews[0]!.candidates).toBe(0);
    expect(previews[0]!.note).toMatch(/geometry currently forbids/);
  });

  it("respects explicit pairs and disabled rules", () => {
    const explicit: BuilderLinkRule = {
      ...ACCESS_RULE,
      rule_id: "explicit",
      topology_mode: "explicit_pairs",
      explicit_pairs: [["ground-near", "leo-sat"]],
    };
    const disabled: BuilderLinkRule = { ...ACCESS_RULE, rule_id: "off", enabled: false };
    const { pairs, previews } = computeCandidates(world([explicit, disabled]));
    expect(pairs).toHaveLength(1);
    expect(previews.find((p) => p.rule_id === "off")!.note).toBe("rule disabled");
  });

  it("rejects pairs beyond the terminals' own range, with the reason noted", () => {
    const w = world([{ ...ACCESS_RULE, rule_id: "tight" }]);
    // Cap both ends' access terminals at 100 km; the overhead satellite sits
    // ~550 km up, so geometry passes but the terminals cannot form it.
    for (const node of w.nodes) {
      node.terminal_inventory = node.terminal_inventory.map((b) => ({
        ...b,
        max_range_km: 100,
      }));
    }
    const { pairs, previews } = computeCandidates(w);
    expect(pairs).toHaveLength(0);
    expect(previews[0]!.note).toContain("beyond terminal range");
  });

  it("never draws more lines per node than it has matching interfaces", () => {
    const w = world([{ ...ACCESS_RULE, rule_id: "cap" }]);
    // Second visible ground near the subsatellite point; satellite access
    // capacity cut to ONE interface: only the nearest pair may draw.
    w.nodes.push(groundNode("ground-near-2", 1.5, SAT_LON_DEG));
    w.link_rules[0] = {
      ...w.link_rules[0]!,
      endpoints: [
        { ...w.link_rules[0]!.endpoints[0], node_ids: ["ground-near", "ground-far", "ground-near-2"] },
        w.link_rules[0]!.endpoints[1],
      ],
    };
    const sat = w.nodes.find((n) => n.node_id === "leo-sat")!;
    sat.terminal_inventory = sat.terminal_inventory.map((b) =>
      b.endpoint_role === "access" ? { ...b, count: 1 } : b,
    );
    const { pairs, previews } = computeCandidates(w);
    expect(pairs).toHaveLength(1);
    expect(pairs[0]!.a).toBe("ground-near");
    expect(previews[0]!.note).toContain("over terminal interface capacity");
  });
});
