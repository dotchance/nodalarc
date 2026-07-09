// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** A wire-faithful two-segment BuilderWorld for link-physics tests.
 *
 *  One ground station and one satellite, each carrying a terminal inventory the
 *  physics derivation reads. Built from the real wire types — the ground node is
 *  `kind: "ground_station"` (not a mislabelled satellite) and the ephemeris is a
 *  real SessionEphemeris, not a bare `{nodes:{}}` cast — so a test driving off
 *  this fixture exercises the same shapes the resolver actually emits. This is
 *  the SHARED home for wire-shape knowledge; candidates.test.ts deliberately
 *  keeps its own physics-tuned numeric fixture (its values are load-bearing for
 *  geometry), a stated non-goal here.
 */
import type { BuilderWorld, BuilderWorldNode } from "../../builderTypes";

type TerminalBlock = BuilderWorldNode["terminal_inventory"][number];

function block(role: string, medium: "rf" | "optical", elev: number | null): TerminalBlock {
  return {
    terminal_id: `${role}_0`,
    owner_node_id: "n",
    endpoint_role: role,
    medium,
    source_terminal_id: null,
    link_role: null,
    count: 1,
    tracking_capacity: null,
    max_range_km: null,
    min_elevation_deg: elev,
    field_of_regard_deg: null,
    tracking_rate_deg_s: null,
    bandwidth_mbps: null,
    source_ref: "x",
  };
}

function node(
  id: string,
  segment: string,
  kind: BuilderWorldNode["kind"],
  blocks: TerminalBlock[],
): BuilderWorldNode {
  return {
    node_id: id,
    local_node_id: id,
    segment_id: segment,
    namespace: null,
    kind,
    plane: null,
    slot: null,
    tags: [],
    surface_position: null,
    forwarding: null,
    terminal_inventory: blocks,
    interfaces: null,
    originated_prefixes: null,
  };
}

/** A ground↔space world: the ground station has an rf access terminal (with an
 *  optional declared elevation floor); the satellite has rf access + optical
 *  isl. groundFloor null ⇒ no terminal declares a floor (the mask is seeded). */
export function tinyWorld(
  groundId: string,
  spaceId: string,
  groundFloor: number | null = 25,
): BuilderWorld {
  return {
    session: { name: "t", display_name: null, description: null },
    epoch_unix: 0,
    ephemeris: {
      epoch_id: 0,
      sim_time: "2026-01-01T00:00:00+00:00",
      epoch_unix: 0,
      nodes: {},
      body_frames: {},
    },
    nodes: [
      node("g1", groundId, "ground_station", [block("access", "rf", groundFloor)]),
      node("s1", spaceId, "satellite", [block("access", "rf", null), block("isl", "optical", null)]),
    ],
    link_rules: [],
    segments: [],
    allocations: [],
    rule_previews: [],
  };
}
