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
}

/** Twin of nodalarc.models.builder_world.BuilderWorld. */
export interface BuilderWorld {
  session: BuilderSessionMeta;
  epoch_unix: number;
  ephemeris: SessionEphemeris;
  nodes: BuilderWorldNode[];
}

/** One row of GET /api/v1/sessions (session_manager.scan_sessions + active flag). */
export interface BuilderSessionListEntry {
  name: string;
  file: string;
  constellation: string;
  routing_stack: string;
  active?: boolean;
}
