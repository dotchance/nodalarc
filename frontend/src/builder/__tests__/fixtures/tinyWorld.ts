// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** A wire-faithful two-segment BuilderWorld for link-physics tests.
 *
 *  One ground station and one satellite, each carrying a terminal inventory the
 *  physics derivation reads. Built from the production wire types: the ground node is
 *  `kind: "ground_station"` (not a mislabelled satellite) and the ephemeris is a
 *  SessionEphemeris, not a bare `{nodes:{}}` cast. Tests using this fixture
 *  exercise the same shapes the resolver emits. candidates.test.ts deliberately
 *  keeps its own physics-tuned numeric fixture (its values are load-bearing for
 *  geometry), which is intentionally outside this fixture.
 */
import type { BuilderWorld, BuilderWorldNode } from "../../builderTypes";

type TerminalBlock = BuilderWorldNode["terminal_inventory"][number];

function block(
  role: TerminalBlock["endpoint_role"],
  medium: "rf" | "optical",
  elev: number,
): TerminalBlock {
  return {
    terminal_id: `${role}_0`,
    owner_node_id: "n",
    endpoint_role: role,
    medium,
    source_terminal_id: null,
    link_role: null,
    count: 1,
    tracking_capacity: 1,
    max_range_km: 5000,
    min_elevation_deg: elev,
    field_of_regard_deg: role === "access" ? 180 : 360,
    tracking_rate_deg_s: 3,
    transmit_mbps: 1000,
    receive_mbps: 1000,
    boresight: null,
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
    epoch_position: null,
    forwarding: null,
    role: "forwarding_only",
    routing_instances: [],
    terminal_inventory: blocks,
    interfaces: null,
    originated_prefixes: null,
  };
}

/** A ground↔space world: the ground station has an rf access terminal with a
 *  declared elevation floor; the satellite has rf access + optical isl. */
export function tinyWorld(
  groundId: string,
  spaceId: string,
  groundFloor = 25,
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
      node("s1", spaceId, "satellite", [block("access", "rf", 0), block("isl", "optical", 0)]),
    ],
    link_rules: [],
    segments: [],
    allocations: [],
    rule_previews: [],
  };
}
