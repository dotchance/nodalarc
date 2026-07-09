// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Document→draft parse core (leaf).
 *
 *  The mint-free, serializer-free core the fork wrappers (workspace.ts, throw
 *  on grammar the editor cannot represent) and the import wrappers
 *  (workspaceImport.ts, collect issues and continue) both consume one-way. The
 *  grammar knowledge and the draft field mapping live ONCE here so the two
 *  paths cannot drift. These helpers never own copy and never mint an id:
 *  parseGroundMember returns an id-free CANDIDATE and each wrapper mints the
 *  member_id on acceptance (counter seam). Type-only imports from
 *  workspace.ts are erased, so there is no runtime cycle.
 */
import type { DraftOrbit, DraftSiteObject } from "./workspace";

/** The reference multi-body session's central body — the default an
 *  un-annotated orbit assumes. Owned here so the parse core is workspace-free. */
export const EARTH_BODY_REF = "nodalarc:bodies/earth.yaml";

export function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, i) => deepEqual(item, b[i]));
  }
  if (typeof a === "object") {
    const ka = Object.keys(a as object);
    const kb = Object.keys(b as object);
    if (ka.length !== kb.length) return false;
    return ka.every((k) =>
      deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
    );
  }
  return false;
}

export function refStem(ref: string): string {
  return (ref.split("/").pop() ?? ref).replace(/\.ya?ml$/, "");
}

/** True when an orbit is element-form (no `shape` block) — the builder edits
 *  shaped orbits only. */
export function orbitLacksShape(orbitRaw: Record<string, unknown>): boolean {
  return (orbitRaw.shape ?? null) === null;
}

/** The propagator the builder cannot author, or null when it is fine. */
export function unsupportedPropagator(orbitRaw: Record<string, unknown>): string | null {
  const propagator = String(orbitRaw.propagator ?? "j2_mean_elements");
  return propagator !== "two_body" && propagator !== "j2_mean_elements" ? propagator : null;
}

/** The DraftOrbit field mapping shared by fork and import. Assumes the caller
 *  has confirmed a `shape` block is present (fork threw / import collected an
 *  issue and returned otherwise). */
export function buildDraftOrbit(orbitRaw: Record<string, unknown>): DraftOrbit {
  const shape = (orbitRaw.shape ?? {}) as Record<string, unknown>;
  const orientation = (orbitRaw.orientation ?? {}) as Record<string, unknown>;
  const phase = (orbitRaw.phase ?? {}) as Record<string, unknown>;
  const propagator = String(orbitRaw.propagator ?? "j2_mean_elements");
  return {
    central_body:
      typeof orbitRaw.central_body === "string" ? orbitRaw.central_body : EARTH_BODY_REF,
    shape_kind: "altitude_km" in shape ? "circular" : "elliptical",
    altitude_km: Number(shape.altitude_km ?? 550),
    perigee_altitude_km: Number(shape.perigee_altitude_km ?? shape.altitude_km ?? 550),
    apogee_altitude_km: Number(shape.apogee_altitude_km ?? shape.altitude_km ?? 550),
    inclination_deg: Number(orientation.inclination_deg ?? 0),
    raan_deg: Number(orientation.raan_deg ?? 0),
    argument_of_perigee_deg: Number(orientation.argument_of_perigee_deg ?? 0),
    mean_anomaly_deg: Number(phase.mean_anomaly_deg ?? 0),
    propagator: propagator as DraftOrbit["propagator"],
  };
}

/** The phasing mode the builder cannot author, or null when it is fine. */
export function unsupportedPhasingMode(constellation: Record<string, unknown>): string | null {
  const phasing = (constellation.phasing ?? {}) as Record<string, unknown>;
  return phasing.mode && phasing.mode !== "evenly_spaced_mean_anomaly"
    ? String(phasing.mode)
    : null;
}

/** The builder authors a constellation's node_tags as exactly [{tag: "all"}]
 *  (every node participates); the draft cannot model a subset. Returns a
 *  description of a non-default node_tags so the fork refuses rather than
 *  silently rewriting it to "all" (the serializer always emits "all"). */
export function nonDefaultNodeTags(constellation: Record<string, unknown>): string | null {
  const tags = constellation.node_tags;
  if (tags === undefined || tags === null) return null;
  if (
    Array.isArray(tags) &&
    tags.length === 1 &&
    (tags[0] as Record<string, unknown> | undefined)?.tag === "all"
  ) {
    return null;
  }
  return JSON.stringify(tags);
}

/** The constellation geometry fields shared by fork and import — the caller
 *  owns identity, display name, orbit, and node policy. */
export function constellationGeometry(constellation: Record<string, unknown>): {
  planes: number;
  raan_spacing_deg: number;
  slots_per_plane: number;
  phase_offset_deg: number;
} {
  const planes = (constellation.planes ?? {}) as Record<string, unknown>;
  const phasing = (constellation.phasing ?? {}) as Record<string, unknown>;
  return {
    planes: Number(planes.count ?? 1),
    raan_spacing_deg: Number(planes.raan_spacing_deg ?? 0),
    slots_per_plane: Number(constellation.slots_per_plane ?? 1),
    phase_offset_deg: Number(phasing.phase_offset_deg ?? 0),
  };
}

export function draftSiteFromDocument(document: Record<string, unknown>): DraftSiteObject {
  const site = (document as { site?: Record<string, unknown> }).site;
  if (!site) throw new Error("not a site document");
  const siteId = String(site.id ?? "");
  const frame = (site.frame ?? {}) as { body_fixed?: { body?: unknown } };
  const body = frame.body_fixed?.body;
  if (typeof body !== "string") {
    // lagrange / ephemeris-anchor frames are a different grammar source,
    // not a surface location — refuse loudly, never drop.
    throw new Error(
      `site ${siteId}: only body-fixed surface sites are editable yet — lagrange and ephemeris-anchor frames are pending`,
    );
  }
  const location = (site.location ?? null) as Record<string, unknown> | null;
  if (!location) throw new Error(`site ${siteId}: non-surface sites are not editable yet`);
  const lan = (site.lan ?? {}) as { ipv4?: unknown };
  if (typeof lan.ipv4 !== "string") {
    throw new Error(`site ${siteId}: IPv6-only sites are not editable yet`);
  }
  const seenNodeIds = new Set<string>();
  const nodes = ((site.nodes as Record<string, unknown>[] | undefined) ?? []).map((node) => {
    const nodeId = String(node.id ?? "gw1");
    if (seenNodeIds.has(nodeId)) {
      // node_id is the editor's stable card key — a duplicate is malformed;
      // refuse loudly (as the other constructs here do), never render two cards
      // under one React key.
      throw new Error(`site ${siteId}: duplicate node id ${nodeId} — node ids must be unique`);
    }
    seenNodeIds.add(nodeId);
    if (typeof node.model !== "string") {
      throw new Error(`site ${siteId}/${nodeId}: inline node models are not editable yet`);
    }
    const payloads = (node.payloads ?? {}) as Record<string, unknown>;
    if (Object.keys(payloads).length > 0) {
      throw new Error(`site ${siteId}/${nodeId}: payload installs are not editable yet`);
    }
    const terminals =
      (node.terminals as Record<string, { installed_count?: number }> | undefined) ?? {};
    const interfaces = (node.interfaces ?? {}) as {
      lo0?: { ipv4?: unknown };
      terr0?: { ipv4?: unknown };
    };
    if (typeof interfaces.lo0?.ipv4 !== "string" || typeof interfaces.terr0?.ipv4 !== "string") {
      throw new Error(`site ${siteId}/${nodeId}: IPv6-only interfaces are not editable yet`);
    }
    return {
      node_id: nodeId,
      model_ref: node.model,
      installed: Object.fromEntries(
        Object.entries(terminals).map(([mount, install]) => [
          mount,
          Number(install.installed_count ?? 1),
        ]),
      ),
      lo0_ipv4: interfaces.lo0.ipv4,
      terr0_ipv4: interfaces.terr0.ipv4,
    };
  });
  if (nodes.length === 0) throw new Error(`site ${siteId} has no nodes`);
  return {
    site_id: siteId,
    display_name: String(site.display_name ?? siteId),
    body,
    lat_deg: Number(location.lat_deg ?? 0),
    lon_deg: Number(location.lon_deg ?? 0),
    alt_m: Number(location.alt_m ?? 0),
    lan_ipv4: lan.ipv4,
    tags: ((site.tags as unknown[] | undefined) ?? []).map(String),
    nodes,
  };
}

/** An id-free ground-member candidate. The wrappers mint the member_id on
 *  acceptance (fork mints eagerly, import mints inside the counter transaction),
 *  so the parse core stays mint-free. */
export type GroundMemberParse =
  | { kind: "ref"; ref: string; site_id: string; label: string }
  | { kind: "draft"; site: DraftSiteObject }
  | { reason: "non-site-entry" };

/** Parse one site-set member entry into an id-free candidate. A ref WITHOUT a
 *  fetched document (import has only the raw YAML) keys its identity on the
 *  ref's filename stem — never a client-side ref-metadata fetch; that stem
 *  assumption is the standing debt-register note. A ref WITH a document (fork
 *  supplies one) reads the document's real id and display name. An inline site
 *  document becomes a draft candidate and may throw from draftSiteFromDocument
 *  — the fork wrapper lets that propagate, the import wrapper catches it. A
 *  document that is neither is reported as a non-site entry (fork throws, import
 *  collects) rather than dropped. */
export function parseGroundMember(
  ref: string | null,
  document: Record<string, unknown> | null,
): GroundMemberParse {
  if (ref !== null && document === null) {
    return { kind: "ref", ref, site_id: refStem(ref), label: refStem(ref) };
  }
  const site = document === null ? undefined : (document as { site?: unknown }).site;
  if (typeof site !== "object" || site === null) {
    return { reason: "non-site-entry" };
  }
  if (ref !== null) {
    const siteObj = site as Record<string, unknown>;
    const siteId = String(siteObj.id ?? "");
    return { kind: "ref", ref, site_id: siteId, label: String(siteObj.display_name ?? siteId) };
  }
  return { kind: "draft", site: draftSiteFromDocument(document as Record<string, unknown>) };
}
