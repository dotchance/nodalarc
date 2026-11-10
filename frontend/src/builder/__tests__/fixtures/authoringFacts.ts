// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
import type { BuilderVisualAuthoringFacts } from "../../generated/builderApi";

export const AUTHORING_FACTS: BuilderVisualAuthoringFacts = {
  default_phasing_mode: "walker_delta",
  default_mount_role: "access",
  default_body_ref: "nodalarc:bodies/earth.yaml",
  mount_roles: [
    { id: "access", label: "access", description: "space ↔ ground" },
    { id: "isl", label: "isl", description: "fabric within a constellation" },
    { id: "crosslink", label: "crosslink", description: "link between constellations" },
    { id: "backbone", label: "backbone", description: "trunk between relay tiers" },
  ],
  link_media: [
    { id: "rf", label: "RF" },
    { id: "optical", label: "optical" },
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
  topology_modes: [
    { id: "visible_candidates", label: "all visible pairs", runtime_supported: true, support_note: null, requires_n: false },
    { id: "nearest_n", label: "nearest N", runtime_supported: true, support_note: null, requires_n: true },
  ],
};
