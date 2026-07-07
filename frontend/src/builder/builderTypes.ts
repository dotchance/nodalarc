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

/** Twin of nodalarc.models.builder_world.BuilderNodeInterfaceFacts. */
export interface BuilderNodeInterfaceFacts {
  node_id: string;
  segment_id: string;
  matching: number;
  free: number;
}

/** Twin of nodalarc.models.builder_world.BuilderRuleAllocation. */
export interface BuilderRuleAllocation {
  rule_id: string;
  kind: string;
  allocated_pairs: number;
  per_node: BuilderNodeInterfaceFacts[];
}

/** Twin of nodalarc.models.builder_world.BuilderLinkCandidate. */
export interface BuilderLinkCandidate {
  rule_id: string;
  node_a: string;
  node_b: string;
}

/** Twin of nodalarc.models.builder_world.BuilderErrorSubject. */
export interface BuilderErrorSubject {
  kind: string;
  id: string;
}

/** Twin of nodalarc.runtime_support.UnsupportedFeature. */
export interface BuilderUnsupportedFeature {
  category: string;
  value: string;
  message: string;
  support_note: string | null;
}

/** Twin of nodalarc.models.builder_world.BuilderResolveRefusal — the 422
 *  envelope preserves what the resolver typed at the raise site (subject
 *  scope, unsupported features). ``error`` is the resolver's message
 *  verbatim. */
export interface BuilderResolveError {
  error: string;
  subject?: BuilderErrorSubject;
  segment_id?: string;
  node_id?: string;
  features?: BuilderUnsupportedFeature[];
}

/** Twin of nodalarc.models.builder_world.BuilderWorldSegment. */
export interface BuilderWorldSegment {
  segment_id: string;
  display_name: string;
}

/** Twin of nodalarc.models.builder_world.BuilderWorld. */
export interface BuilderWorld {
  session: BuilderSessionMeta;
  epoch_unix: number;
  ephemeris: SessionEphemeris;
  nodes: BuilderWorldNode[];
  link_rules: BuilderLinkRule[];
  segments: BuilderWorldSegment[];
  allocations: BuilderRuleAllocation[];
  link_candidates: BuilderLinkCandidate[];
}

/** Twin of nodalarc.models.builder_world.BuilderResolveCheck. */
export interface BuilderResolveCheck {
  world: BuilderWorld;
  document: Record<string, unknown>;
  /** The pane/resolution document — the authoring shape, not the save
   *  artifact (a save flattens user references first). */
  document_yaml: string;
  /** Sha256 of the canonical flattened YAML a save of this document writes:
   *  hypothetical on a resolve check, exact on a save. */
  artifact_sha256: string;
  /** Runtime-readiness (Q3), node-count-independent subset: a session may
   *  resolve and save yet be unable to start on the cluster. Necessary, not
   *  sufficient — the switch endpoint runs the full node-count-aware validator. */
  deploy_ready: boolean;
  deploy_blockers: string[];
}

/** Twin of nodalarc.models.builder_world.BuilderCatalogEntry. */
export interface BuilderCatalogEntry {
  ref: string;
  family: string;
  id: string | null;
  display_name: string | null;
  notes: string | null;
  summary: string | null;
  error: string | null;
}

/** One row of GET /api/v1/sessions (session_manager.scan_sessions + active flag). */
export interface BuilderSessionListEntry {
  name: string;
  file: string;
  constellation: string;
  routing_stack: string;
  /** The root tier the file lives under — never provenance: the generated
   *  root holds builder, wizard, and upload outputs alike, all "user". */
  source: "user" | "nodalarc";
  active?: boolean;
}
