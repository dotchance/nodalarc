// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder world → Scene input adapter.
 *
 *  The R3F Scene is the single production globe; its input contract is a
 *  StateSnapshot + SessionEphemeris. For a resolved-but-not-deployed session
 *  there is no runtime snapshot, so this module derives one from the
 *  BuilderWorld: node identities from the resolver, satellite positions
 *  propagated at the session epoch with the SAME client math the live globe
 *  runs (sim/ephemeris twin of the backend propagator), ground positions
 *  from the resolver's surface_position (the ephemeris only carries
 *  space-link participants; the world carries every node).
 *
 *  This object exists ONLY as Scene input. Fields the Scene never reads but
 *  the StateSnapshot type requires (network_health, session ids, ops fields)
 *  get neutral values — never surface them in builder UI as session state.
 */

import type { NodeState, StateSnapshot } from "../types";
import {
  bodyMathFromFrame,
  kmPerRenderUnitFromEphemeris,
  propagateNode,
  type EphemerisNode,
} from "../sim/ephemeris";
import type { BuilderWorld, BuilderWorldNode } from "./builderTypes";

function _satelliteNodeState(
  node: BuilderWorldNode,
  entry: EphemerisNode,
  world: BuilderWorld,
): NodeState {
  if (entry.type === "tle") {
    throw new Error(
      `builder cannot seed TLE-propagated satellite ${node.node_id}; ` +
        "SGP4 sessions are not supported in the builder yet",
    );
  }
  const frame = world.ephemeris.body_frames[entry.reference_body];
  if (!frame) {
    throw new Error(
      `builder world is missing body frame ${entry.reference_body} for ${node.node_id}`,
    );
  }
  const kmPerRenderUnit = kmPerRenderUnitFromEphemeris(world.ephemeris);
  const position = propagateNode(
    entry,
    world.epoch_unix,
    world.epoch_unix,
    bodyMathFromFrame(frame, kmPerRenderUnit),
  );
  return {
    node_id: node.node_id,
    node_type: node.kind,
    lat_deg: position.latDeg,
    lon_deg: position.lonDeg,
    alt_km: position.altKm,
    vel_x_km_s: position.velXKmS,
    vel_y_km_s: position.velYKmS,
    vel_z_km_s: position.velZKmS,
    plane: node.plane,
    slot: node.slot,
    routing_area: null,
    neighbor_count: 0,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    min_elevation_deg: null,
    beam_falloff_exponent: null,
    reference_body: entry.reference_body,
    frame_id: entry.frame_id,
    segment_id: node.segment_id,
    local_node_id: node.local_node_id,
    namespace: node.namespace,
    tags: node.tags,
  };
}

function _groundNodeState(node: BuilderWorldNode): NodeState {
  const surface = node.surface_position;
  if (!surface) {
    throw new Error(`builder world node ${node.node_id} (${node.kind}) has no placement`);
  }
  return {
    node_id: node.node_id,
    node_type: node.kind,
    lat_deg: surface.lat_deg,
    lon_deg: surface.lon_deg,
    alt_km: surface.alt_m / 1000,
    vel_x_km_s: 0,
    vel_y_km_s: 0,
    vel_z_km_s: 0,
    plane: node.plane,
    slot: node.slot,
    routing_area: null,
    neighbor_count: 0,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    min_elevation_deg: null,
    beam_falloff_exponent: null,
    reference_body: surface.body,
    frame_id: surface.body,
    segment_id: node.segment_id,
    local_node_id: node.local_node_id,
    namespace: node.namespace,
    tags: node.tags,
  };
}

/** Derive the Scene-input snapshot for a resolved world, frozen at its epoch. */
export function builderSnapshotFromWorld(world: BuilderWorld): StateSnapshot {
  const nodes: NodeState[] = world.nodes.map((node) => {
    const entry = world.ephemeris.nodes[node.node_id];
    if (node.kind === "satellite") {
      if (!entry) {
        throw new Error(`builder world satellite ${node.node_id} has no ephemeris entry`);
      }
      return _satelliteNodeState(node, entry, world);
    }
    return _groundNodeState(node);
  });

  return {
    sim_time: world.ephemeris.sim_time,
    wall_time: world.ephemeris.sim_time,
    schema_version: 0,
    session_id: `builder:${world.session.name}`,
    nodes,
    links: [],
    traced_paths: [],
    active_flows: [],
    recent_events: [],
    network_health: {
      status: "converged",
      converging_since_ms: null,
      unreachable_flows: 0,
      last_convergence_ms: null,
    },
    routing_stack: null,
    constellation_name: `builder:${world.session.name}`,
    session_status: null,
    session_status_detail: null,
    playback_paused: true,
    playback_speed: 1,
    client_arrival_ms: performance.now(),
    stale: false,
  };
}
