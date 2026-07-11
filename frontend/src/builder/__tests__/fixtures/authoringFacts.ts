import type { BuilderVisualAuthoringFacts } from "../../generated/builderApi";

export const AUTHORING_FACTS: BuilderVisualAuthoringFacts = {
  default_phasing_mode: "walker_delta",
  single_plane_phasing_mode: "evenly_spaced_mean_anomaly",
  default_scheduling_preset: "leo-fast-handover",
  default_mount_role: "access",
  default_terminal_mount_count: 1,
  default_body_ref: "nodalarc:bodies/earth.yaml",
  default_node: {
    id: "my-node",
    display_name: "My node",
    forwarding: null,
    ethernet: [],
    terminals: [],
  },
  space_access_boresight: { mode: "nadir" },
  ground_access_boresight: { mode: "local_vertical" },
  mount_roles: [
    { id: "access", label: "access", description: "space ↔ ground" },
    { id: "isl", label: "isl", description: "fabric within a constellation" },
    { id: "crosslink", label: "crosslink", description: "link between constellations" },
    { id: "backbone", label: "backbone", description: "trunk between relay tiers" },
  ],
  link_media: [
    { id: "rf", label: "RF", signal_seed: { band: "", frequency_hz: 0 } },
    { id: "optical", label: "optical", signal_seed: { wavelength_nm: 0 } },
  ],
  forwarding_classes: [
    { id: "routed", label: "routed" },
    { id: "host", label: "host" },
    { id: "bridge", label: "bridge" },
    { id: "control_only", label: "control only" },
  ],
  routing_protocols: [
    { id: "isis", label: "IS-IS", runtime_supported: true, support_note: null, timer_fields: true },
    { id: "ospf", label: "OSPF", runtime_supported: true, support_note: null, timer_fields: true },
    { id: "bgp", label: "BGP", runtime_supported: false, support_note: "planned", timer_fields: false },
    { id: "static", label: "static", runtime_supported: true, support_note: null, timer_fields: false },
  ],
  boundary_adapters: [
    { id: "static_ip", label: "static IP", runtime_supported: true, support_note: null },
    { id: "bgp", label: "BGP", runtime_supported: false, support_note: "planned" },
    { id: "dtn_bundle", label: "DTN bundle", runtime_supported: false, support_note: "planned" },
  ],
  phasing_modes: [
    { id: "walker_delta", label: "Walker delta" },
    { id: "walker_star", label: "Walker star" },
    { id: "evenly_spaced_mean_anomaly", label: "single-plane evenly spaced" },
  ],
  orbit_shapes: [
    { id: "circular", label: "circular" },
    { id: "elliptical", label: "elliptical" },
  ],
  orbit_propagators: [
    { id: "two_body", label: "two body", runtime_supported: true, support_note: null },
    { id: "j2_mean_elements", label: "J2 mean elements", runtime_supported: true, support_note: null },
  ],
  topology_modes: [
    { id: "visible_candidates", label: "all visible pairs", runtime_supported: true, support_note: null, requires_n: false },
    { id: "nearest_n", label: "nearest N", runtime_supported: true, support_note: null, requires_n: true },
  ],
};
