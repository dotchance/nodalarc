import type {
  DraftBoundary,
  DraftConstellation,
  DraftGroundSet,
  DraftGroundSite,
  DraftNode,
  DraftRoutingDomain,
  GroundBoresight,
  Workspace,
} from "../../workspace";

export const EARTH_BODY_REF = "nodalarc:bodies/earth.yaml";

let spaceCounter = 0;
let groundCounter = 0;
let domainCounter = 0;
let boundaryCounter = 0;
let siteCounter = 0;

export function newWorkspace(name: string): Workspace {
  return {
    session_name: name,
    display_name: null,
    description: null,
    space: [],
    space_refs: [],
    ground: [],
    ground_refs: [],
    links: [],
    routing_domains: [],
    boundaries: [],
    max_pairs_per_rule: 2_000,
    max_pairs_per_tick: 10_000,
    start_time: "2026-01-01T00:00:00Z",
    step_seconds: 1,
    compression: 1,
    projection_revision: 0,
    control_tree: null,
  };
}

export function newDraftConstellation(nodeRef: string): DraftConstellation {
  spaceCounter += 1;
  return {
    segment_id: `space-${spaceCounter}`,
    display_name: `Constellation ${spaceCounter}`,
    node_ref: nodeRef,
    node_draft: null,
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

export function defaultDraftNode(): DraftNode {
  return {
    id: "my-node",
    display_name: "My node",
    forwarding: null,
    profile: null,
    ethernet: [],
    terminals: [],
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
      lan_base: `172.${20 + ((groundCounter - 1) % 12)}`,
      loopback_base: `10.${200 + ((groundCounter - 1) % 55)}`,
    },
    scheduling: {},
    originated_ipv4: [],
    tags: [],
  };
}

export function testGroundMember(
  ground: DraftGroundSet,
  name: string,
  latDeg: number,
  lonDeg: number,
  addressIndex = 0,
): DraftGroundSite {
  siteCounter += 1;
  const siteId = `test-site-${siteCounter}`;
  return {
    member_id: `test-member-${siteCounter}`,
    kind: "draft",
    ref: null,
    site_id: siteId,
    label: name,
    summary: null,
    scheduling_override: null,
    site: {
      site_id: siteId,
      display_name: name,
      body: ground.stamp.body,
      lat_deg: latDeg,
      lon_deg: lonDeg,
      alt_m: 0,
      lan_ipv4: `${ground.stamp.lan_base}.${addressIndex}.0/24`,
      tags: [],
      nodes: [{
        node_id: "gw1",
        node_ref: ground.stamp.node_ref,
        installed: { ...ground.stamp.installed },
        boresights: { ...ground.stamp.boresights },
        lo0_ipv4: `${ground.stamp.loopback_base}.0.${addressIndex + 1}/32`,
        terr0_ipv4: `${ground.stamp.lan_base}.${addressIndex}.1/24`,
      }],
    },
  };
}

export function defaultRoutingDomain(workspace: Workspace): DraftRoutingDomain {
  domainCounter += 1;
  return {
    domain_id: `domain-${domainCounter}`,
    label: `domain ${domainCounter}`,
    protocol: "isis",
    member_segment_ids: [
      ...workspace.space.map((draft) => draft.segment_id),
      ...workspace.space_refs.map((draft) => draft.segment_id),
      ...workspace.ground.map((draft) => draft.segment_id),
      ...workspace.ground_refs.map((draft) => draft.segment_id),
    ],
    hello_interval_s: null,
    hold_interval_s: null,
  };
}

export function defaultBoundary(workspace: Workspace): DraftBoundary {
  boundaryCounter += 1;
  return {
    boundary_id: `boundary-${boundaryCounter}`,
    over_rule_id: workspace.links[0]?.rule_id ?? "",
    adapter: "static_ip",
    from_domain_id: workspace.routing_domains[0]?.domain_id ?? "",
    to_domain_id:
      workspace.routing_domains[1]?.domain_id ??
      workspace.routing_domains[0]?.domain_id ??
      "",
    export_node_loopbacks: true,
  };
}
