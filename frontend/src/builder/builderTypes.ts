// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder wire types — exact twins of lib/nodalarc/models/builder_world.py.
 *
 *  Field names mirror the backend pydantic models byte-for-byte (snake_case,
 *  no mapping layer) so the backend contract test can pin both directions.
 *  The ephemeris payload reuses the SessionEphemeris twin from sim/ephemeris.
 */

import type { SessionEphemeris } from "../sim/ephemeris";

/** Twin of nodalarc.models.segment_session.SessionMeta. */
export interface BuilderSessionMeta {
  name: string;
  display_name: string | null;
  description: string | null;
}

/** Twin of nodalarc.models.resolved_session.ResolvedSurfacePosition. */
export interface BuilderSurfacePosition {
  body: string;
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
}

/** Twin of nodalarc.models.resolved_session.ResolvedTerminalBlock. */
export interface ResolvedTerminalBlock {
  terminal_id: string;
  owner_node_id: string;
  endpoint_role: string;
  medium: "rf" | "optical";
  source_terminal_id: string | null;
  link_role: string | null;
  count: number;
  tracking_capacity: number | null;
  max_range_km: number | null;
  min_elevation_deg: number | null;
  field_of_regard_deg: number | null;
  tracking_rate_deg_s: number | null;
  bandwidth_mbps: number | null;
  source_ref: string;
}

/** Twin of nodalarc.models.resolved_session.ResolvedInterfaceAddress. */
export interface ResolvedInterfaceAddress {
  ipv4: string | null;
  ipv6: string | null;
}

/** Twin of nodalarc.models.resolved_session.ResolvedNodeInterfaces. */
export interface ResolvedNodeInterfaces {
  lo0: ResolvedInterfaceAddress;
  terr0: ResolvedInterfaceAddress | null;
}

/** Twin of nodalarc.models.segments.OriginatedPrefixes. */
export interface OriginatedPrefixes {
  ipv4: string[] | null;
  ipv6: string[] | null;
}

/** Twin of nodalarc.models.builder_world.BuilderWorldNode. */
export interface BuilderWorldNode {
  node_id: string;
  local_node_id: string;
  segment_id: string;
  namespace: string | null;
  kind: "satellite" | "ground_station" | "relay";
  plane: number | null;
  slot: number | null;
  tags: string[];
  surface_position: BuilderSurfacePosition | null;
  forwarding: "routed" | "host" | "bridge" | "control_only" | null;
  terminal_inventory: ResolvedTerminalBlock[];
  interfaces: ResolvedNodeInterfaces | null;
  originated_prefixes: OriginatedPrefixes | null;
}

/** Twin of nodalarc.models.builder_world.BuilderLinkEndpoint. */
export interface BuilderLinkEndpoint {
  segment_id: string;
  terminal_role: string;
  terminal_medium: string | null;
  min_elevation_deg: number | null;
  node_ids: string[];
}

/** Twin of nodalarc.models.builder_world.BuilderLinkRule. */
export interface BuilderLinkRule {
  rule_id: string;
  kind: string;
  enabled: boolean;
  endpoints: [BuilderLinkEndpoint, BuilderLinkEndpoint];
  topology_mode: string;
  topology_n: number | null;
  explicit_pairs: [string, string][];
  max_range_km: number | null;
}

/** Twin of nodalarc.models.builder_world.BuilderWorld. */
export interface BuilderWorld {
  session: BuilderSessionMeta;
  epoch_unix: number;
  ephemeris: SessionEphemeris;
  nodes: BuilderWorldNode[];
  link_rules: BuilderLinkRule[];
}

/** Twin of nodalarc.models.builder_world.BuilderResolveCheck. */
export interface BuilderResolveCheck {
  world: BuilderWorld;
  document_yaml: string;
}

/** Twin of nodalarc.models.builder_world.BuilderCatalogEntry. */
export interface BuilderCatalogEntry {
  ref: string;
  family: string;
  id: string | null;
  display_name: string | null;
  notes: string | null;
  error: string | null;
}

/** One row of GET /api/v1/sessions (session_manager.scan_sessions + active flag). */
export interface BuilderSessionListEntry {
  name: string;
  file: string;
  constellation: string;
  routing_stack: string;
  active?: boolean;
}
