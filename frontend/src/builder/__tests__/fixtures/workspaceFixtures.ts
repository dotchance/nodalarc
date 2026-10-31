import type {
  DraftConstellation,
  DraftGroundSet,
  GroundBoresight,
  Workspace,
} from "../../workspace";

export const EARTH_BODY_REF = "nodalarc:bodies/earth.yaml";

let spaceCounter = 0;
let groundCounter = 0;

export function newWorkspace(name: string): Workspace {
  return {
    session_name: name,
    display_name: null,
    description: null,
    space: [],
    space_refs: [],
    ground: [],
    ground_refs: [],
    max_pairs_per_rule: 2_000,
    max_pairs_per_tick: 10_000,
    start_time: "2026-01-01T00:00:00Z",
    step_seconds: 1,
    compression: 1,
  };
}

export function newDraftConstellation(nodeRef: string): DraftConstellation {
  spaceCounter += 1;
  return {
    segment_id: `space-${spaceCounter}`,
    display_name: `Constellation ${spaceCounter}`,
    node_ref: nodeRef,
    orbit: {
      central_body: EARTH_BODY_REF,
      shape_kind: "circular",
      altitude_km: 550,
      perigee_altitude_km: 550,
      apogee_altitude_km: 550,
      inclination_deg: 53,
      raan_deg: 0,
      argument_of_perigee_deg: 0,
      mean_anomaly_deg: 0,
      propagator: "j2_mean_elements",
    },
    planes: 3,
    raan_spacing_deg: 60,
    slots_per_plane: 8,
    phasing_mode: "walker_delta",
    phase_offset_deg: 0,
  };
}


export function newDraftGroundSet(
  nodeRef: string,
  installed: Record<string, number>,
  body = EARTH_BODY_REF,
  boresights: Record<string, GroundBoresight> = {},
): DraftGroundSet {
  groundCounter += 1;
  return {
    segment_id: `ground-${groundCounter}`,
    display_name: `Ground segment ${groundCounter}`,
    members: [],
    stamp: {
      node_ref: nodeRef,
      installed,
      boresights,
      body,
    },
    scheduling: {},
    originated_ipv4: [],
    tags: [],
  };
}
