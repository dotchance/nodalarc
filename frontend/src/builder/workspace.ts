// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Transient browser state for the visual session authoring surface.
 *
 * Persisted session/catalog grammar is assembled and validated by VS-API. This
 * module owns only interaction state, editor defaults, and local guidance.
 */

import type {
  BuilderVisualLinkEndpoint,
  BuilderVisualNode,
  BuilderVisualGroundBoresight,
  BuilderVisualOrbitPropagator,
  BuilderVisualOrbitShape,
  BuilderVisualRoutingBoundary,
  BuilderVisualRoutingDomain,
  BuilderVisualSpaceBoresight,
  BuilderVisualSpaceDraft,
  BuilderVisualTerminalMount,
  BuilderVisualTopologyMode,
} from "./generated/builderApi";

/** UI labels over generated visual-authoring application types. */
export type MountRole = NonNullable<BuilderVisualTerminalMount["role"]>;
export type LinkMedium = NonNullable<BuilderVisualLinkEndpoint["medium"]>;
export type OrbitShape = BuilderVisualOrbitShape;
export type Propagator = BuilderVisualOrbitPropagator;
export type TopologyMode = BuilderVisualTopologyMode;
export type Protocol = NonNullable<BuilderVisualRoutingDomain["protocol"]>;
export type Adapter = NonNullable<BuilderVisualRoutingBoundary["adapter"]>;
export type Forwarding = NonNullable<BuilderVisualNode["forwarding"]>;
export type PhasingMode = BuilderVisualSpaceDraft["phasing_mode"];
export type SpaceBoresight = BuilderVisualSpaceBoresight;
export type GroundBoresight = BuilderVisualGroundBoresight;
export interface DraftOrbit {
  /** The body this orbit is around — a bodies-catalog ref, serialized
   *  verbatim. The runtime decides what it supports; unsupported bodies get
   *  the resolver's typed wall, never a silent earth default. */
  central_body: string;
  /** Circular uses altitude_km; elliptical uses perigee/apogee. One form
   *  serializes (the grammar's OrbitShape variants); the other fields are
   *  kept so switching shape kinds never loses typed values. */
  shape_kind: OrbitShape;
  altitude_km: number;
  perigee_altitude_km: number;
  apogee_altitude_km: number;
  inclination_deg: number;
  raan_deg: number;
  argument_of_perigee_deg: number;
  mean_anomaly_deg: number;
  propagator: Propagator;
}

export interface DraftTerminalMount {
  mount_id: string;
  role: MountRole;
  terminal_ref: string;
  count: number;
  boresight: SpaceBoresight | null;
}

/** An editable node: the grammar's Node object as draft state. LAN attach is
 *  ``ethernet`` ports (the builder's "lan" chips serialize there — LAN is not
 *  a terminal role). */
export interface DraftNode {
  id: string;
  display_name: string;
  forwarding: Forwarding | null;
  ethernet: string[];
  terminals: DraftTerminalMount[];
}

export interface DraftConstellation {
  segment_id: string;
  display_name: string;
  /** The node model reference (shipped or user). When ``node_draft`` is set,
   *  it is transient session application state compiled by VS-API. */
  node_ref: string;
  node_draft: DraftNode | null;
  orbit: DraftOrbit;
  planes: number;
  raan_spacing_deg: number;
  slots_per_plane: number;
  phasing_mode: PhasingMode;
  phase_offset_deg: number;
}

/** One node installed at a site: a model plus what's actually mounted and
 *  how it addresses. The node is the router; the site is the place. */
export interface DraftSiteNode {
  node_id: string;
  model_ref: string;
  /** Installed count per mount id, seeded from the model's faceplate. */
  installed: Record<string, number>;
  /** Explicit ground pointing per installed access mount. */
  boresights: Record<string, GroundBoresight>;
  lo0_ipv4: string;
  terr0_ipv4: string;
}

/** A site is a first-class primitive — the terminals, nodes, networks, and
 *  parameters that make up a location, not just a lat/lon. This is the
 *  grammar's Site object as an editable draft (IPv4-only for now).
 *  ``body`` is the surface the site stands on — a bodies-catalog ref
 *  serialized verbatim into the grammar's body-fixed frame, exactly as an
 *  orbit's central_body: a location is a (body, lat, lon), never a lat/lon
 *  with Earth assumed. */
export interface DraftSiteObject {
  site_id: string;
  display_name: string;
  body: string;
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
  lan_ipv4: string;
  tags: string[];
  nodes: DraftSiteNode[];
}

/** One member of a ground segment: a defined site — placed by reference at
 *  full fidelity (its nodes travel with it), or an authored draft. */
export interface DraftGroundSite {
  /** Stable list key, builder-local. */
  member_id: string;
  kind: "ref" | "draft";
  /** Catalog reference when kind=ref. */
  ref: string | null;
  /** The site's grammar id — override matching keys on this. */
  site_id: string;
  label: string;
  /** Hardware line for ref rows (from the catalog browse). */
  summary: string | null;
  site: DraftSiteObject | null;
  /** Sparse per-site scheduling: null = segment template ("= template");
   *  a scheduling block = an override stored as a GroundOverride exception.
   *  Carried as grammar data (round-trips verbatim), produced by presets. */
  scheduling_override: Record<string, unknown> | null;
}

/** The stamp: what bulk paste mints new sites with — a node model and
 *  addressing bases. Applied once at creation; every minted site owns its
 *  configuration afterwards (edit the site, not the stamp). */
export interface GroundStamp {
  node_ref: string;
  installed: Record<string, number>;
  boresights: Record<string, GroundBoresight>;
  /** The body minted sites stand on — seeds each mint; every site owns its
   *  own body afterwards. */
  body: string;
  /** IPv4 base for minted site LANs: mint i gets base.<i>.0/24, terr0 .1. */
  lan_base: string;
  /** IPv4 base for minted loopbacks: mint i gets base.0.<i+1>/32. */
  loopback_base: string;
}

/** An editable ground segment: a combination of defined sites, plus the
 *  session-level application (scheduling intent, originated prefixes, tags,
 *  sparse per-site overrides). */
export interface DraftGroundSet {
  segment_id: string;
  display_name: string;
  members: DraftGroundSite[];
  stamp: GroundStamp;
  /** The ground scheduling block returned by VS-API, carried opaquely so a
   *  session round-trips without browser-side interpretation. */
  scheduling: Record<string, unknown>;
  /** apply-level originated prefixes (routing injection intent). */
  originated_ipv4: string[];
  tags: string[];
}

/** A space segment sourced from a library block as-is (use-this-block).
 *  Customizing forks it into an editable DraftConstellation. */
export interface RefSegment {
  segment_id: string;
  ref: string;
  label: string;
}

/** One endpoint of an authored link rule: a placed segment, optionally
 *  scoped to a tag (how one ground segment serves multiple constellations
 *  differently), with the terminal role/medium it selects and an optional
 *  elevation mask. */
export interface DraftLinkEndpoint {
  segment_id: string;
  tag: string | null;
  role: MountRole;
  medium: LinkMedium;
  min_elevation_deg: number | null;
}

/** An authored link rule — comms intent between placed segments. Rules
 *  declare who may link; OME computes feasibility from geometry, terminal
 *  limits, and runtime state. */
export interface DraftLinkRule {
  rule_id: string;
  label: string;
  enabled: boolean;
  a: DraftLinkEndpoint;
  b: DraftLinkEndpoint;
  /** nearest_visible exists in the grammar but is runtime-gated — never
   *  offered here; the resolver walls it with UnsupportedFeature. */
  topology_mode: TopologyMode;
  topology_n: number;
  max_range_km: number | null;
}

/** An authored routing domain: a protocol over member segments. Whole-
 *  segment membership only — per-terminal membership is a gated grammar
 *  change and walls at the gesture. Timers are the expert card: null =
 *  engine defaults (omitted from the session document). */
export interface DraftRoutingDomain {
  domain_id: string;
  label: string;
  protocol: Protocol;
  member_segment_ids: string[];
  hello_interval_s: number | null;
  hold_interval_s: number | null;
}

/** An authored routing boundary: a controlled exchange over a link rule.
 *  v1 export is the shipped exchange pattern — originated prefixes both
 *  ways, installed via peer loopback. */
export interface DraftBoundary {
  boundary_id: string;
  over_rule_id: string;
  adapter: Adapter;
  from_domain_id: string;
  to_domain_id: string;
  export_node_loopbacks: boolean;
}

export interface Workspace {
  name: string;
  /** Session-level human-facing metadata (SessionMeta.display_name /
   *  description). Carried verbatim so an imported session round-trips its
   *  labels; null when the session declares none (the grammar defaults them
   *  to None, so a null is omitted from the emitted session block). */
  display_name: string | null;
  description: string | null;
  space: DraftConstellation[];
  /** Library constellations placed by reference (use-this-block). */
  space_refs: RefSegment[];
  /** Authored ground segments (drafts) — plural by design: teleport, edge,
   *  and experiment sets carry different scheduling in one session. */
  ground: DraftGroundSet[];
  /** Library site sets placed by reference (use-this-block). */
  ground_refs: RefGroundSet[];
  /** Authored comms intent between placed segments. */
  links: DraftLinkRule[];
  /** Authored routing domains + boundaries between them. */
  routing_domains: DraftRoutingDomain[];
  boundaries: DraftBoundary[];
  /** Candidate math budget — the grammar requires declared limits once link
   *  rules exist in a multi-segment session (no silent defaults). Sized to
   *  the largest shipped session; typeable like everything else. */
  max_pairs_per_rule: number;
  max_pairs_per_tick: number;
  start_time: string;
  /** Sim step and wall-clock compression — 1/1 is real time (the time-rate
   *  invariant: deviation is an explicit manipulation, never a default). */
  step_seconds: number;
  compression: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isGroundBoresightMap(value: unknown): boolean {
  return (
    isRecord(value) &&
    Object.values(value).every(
      (boresight) => isRecord(boresight) && typeof boresight.mode === "string",
    )
  );
}

/** Require the complete current browser DTO shape without interpreting its
 *  grammar-valued fields; backend compilation owns their allowed values and
 *  semantics. */
export function hasRequiredAuthoringState(value: unknown): boolean {
  if (!isRecord(value) || !Array.isArray(value.space) || !Array.isArray(value.ground)) {
    return false;
  }
  const currentSpace = value.space.every((draft) => {
    if (
      !isRecord(draft) ||
      typeof draft.planes !== "number" ||
      !Number.isFinite(draft.planes) ||
      typeof draft.phase_offset_deg !== "number" ||
      !Number.isFinite(draft.phase_offset_deg) ||
      typeof draft.phasing_mode !== "string" ||
      draft.phasing_mode.length === 0
    ) {
      return false;
    }
    if (draft.node_draft === null) return true;
    if (!isRecord(draft.node_draft) || !Array.isArray(draft.node_draft.terminals)) {
      return false;
    }
    return draft.node_draft.terminals.every(
      (mount) =>
        isRecord(mount) &&
        "boresight" in mount &&
        (mount.boresight === null ||
          (isRecord(mount.boresight) && typeof mount.boresight.mode === "string")),
    );
  });
  const currentGround = value.ground.every(
    (draft) =>
      isRecord(draft) &&
      isRecord(draft.stamp) &&
      isGroundBoresightMap(draft.stamp.boresights) &&
      Array.isArray(draft.members) &&
      draft.members.every(
        (member) =>
          isRecord(member) &&
          (member.site === null ||
            (isRecord(member.site) &&
              Array.isArray(member.site.nodes) &&
              member.site.nodes.every(
                (node) => isRecord(node) && isGroundBoresightMap(node.boresights),
              ))),
      ),
  );
  return currentSpace && currentGround;
}

/** Accept only the current complete browser-workspace shape. */
export function isCurrentWorkspace(value: unknown): value is Workspace {
  if (!isRecord(value)) return false;
  const currentSiteMembers = (members: unknown[]): boolean =>
    members.every((member) => {
      if (!isRecord(member)) return false;
      if (member.site === null) return true;
      return isRecord(member.site) && typeof member.site.body === "string";
    });
  return (
    hasRequiredAuthoringState(value) &&
    typeof value.name === "string" &&
    (value.display_name === null || typeof value.display_name === "string") &&
    (value.description === null || typeof value.description === "string") &&
    typeof value.start_time === "string" &&
    typeof value.step_seconds === "number" &&
    Number.isFinite(value.step_seconds) &&
    typeof value.compression === "number" &&
    Number.isFinite(value.compression) &&
    typeof value.max_pairs_per_rule === "number" &&
    Number.isFinite(value.max_pairs_per_rule) &&
    typeof value.max_pairs_per_tick === "number" &&
    Number.isFinite(value.max_pairs_per_tick) &&
    Array.isArray(value.space) &&
    value.space.every(
      (draft) =>
        isRecord(draft) &&
        isRecord(draft.orbit) &&
        typeof draft.orbit.central_body === "string",
    ) &&
    Array.isArray(value.space_refs) &&
    Array.isArray(value.ground) &&
    value.ground.every(
      (draft) =>
        isRecord(draft) &&
        isRecord(draft.stamp) &&
        typeof draft.stamp.body === "string" &&
        Array.isArray(draft.members) &&
        currentSiteMembers(draft.members),
    ) &&
    Array.isArray(value.ground_refs) &&
    Array.isArray(value.links) &&
    Array.isArray(value.routing_domains) &&
    Array.isArray(value.boundaries)
  );
}

let refCounter = 0;

export function newRefSegment(ref: string, label: string): RefSegment {
  refCounter += 1;
  return { segment_id: `lib-${refCounter}`, ref, label };
}

/** A site set placed by reference, plus the session-owned scheduling intent
 *  (scheduling is a session concern — site-set documents never carry it). */
export interface RefGroundSet extends RefSegment {
  scheduling: Record<string, unknown>;
}

let memberCounter = 0;

/** The grammar id a member answers to (override matching keys on it). */
// Re-exported: the moved importer matches override targets by site id;
// de-exported it while every consumer was internal.
export function memberSiteId(member: DraftGroundSite): string {
  return member.kind === "draft" && member.site ? member.site.site_id : member.site_id;
}

export interface ParsedSiteLine {
  name: string;
  lat_deg: number;
  lon_deg: number;
  alt_m: number;
}

/** Bulk site paste: one site per line, ``name, lat, lon[, alt_m]`` — commas
 *  or tabs (spreadsheet columns paste as tabs). Bad lines are reported,
 *  never silently dropped. */
export function parseSiteLines(text: string): { rows: ParsedSiteLine[]; errors: string[] } {
  const rows: ParsedSiteLine[] = [];
  const errors: string[] = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const parts = line
      .split(/[\t,]/)
      .map((part) => part.trim())
      .filter((part) => part.length > 0);
    if (parts.length < 3) {
      errors.push(`"${line}" — expected: name, lat, lon`);
      continue;
    }
    const name = parts[0] ?? "";
    const lat = Number(parts[1]);
    const lon = Number(parts[2]);
    const alt = parts.length > 3 ? Number(parts[3]) : 0;
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(alt)) {
      errors.push(`"${line}" — lat, lon, alt must be numbers`);
      continue;
    }
    if (!name) {
      errors.push(`"${line}" — name is required`);
      continue;
    }
    rows.push({ name, lat_deg: lat, lon_deg: lon, alt_m: alt });
  }
  return { rows, errors };
}

/** A defined site placed by reference — full fidelity, its nodes travel
 *  with it. site_id must be the document's grammar id (read at add time). */
export function refGroundMember(
  ref: string,
  siteId: string,
  label: string,
  summary: string | null,
): DraftGroundSite {
  memberCounter += 1;
  return {
    member_id: `member-${memberCounter}`,
    kind: "ref",
    ref,
    site_id: siteId,
    label,
    summary,
    site: null,
    scheduling_override: null,
  };
}

/** Ground sanity findings: warn, never block (a polar site under an
 *  equatorial shell is a learning path; only the structurally broken and
 *  the off-the-map get flagged, in plain language). */
export function groundWarnings(draft: DraftGroundSet): string[] {
  const warnings: string[] = [];
  const seenIds = new Set<string>();
  for (const member of draft.members) {
    const id = memberSiteId(member);
    if (seenIds.has(id)) {
      warnings.push(`duplicate site id "${id}" — sites are places and exist once`);
    }
    seenIds.add(id);
    const site = member.site;
    if (!site) continue;
    if (Math.abs(site.lat_deg) > 90) {
      warnings.push(`${site.display_name}: latitude ${site.lat_deg} is off the map (±90)`);
    }
    if (Math.abs(site.lon_deg) > 180) {
      warnings.push(`${site.display_name}: longitude ${site.lon_deg} is off the map (±180)`);
    }
  }
  return warnings;
}

/** Every placed segment a link rule can select, with its kind — the role
 *  defaults key on kinds (space⟲space=isl, space↔space=crosslink,
 *  ground↔space=access). */
export interface PlacedSegment {
  segment_id: string;
  label: string;
  kind: "space" | "ground";
}

export function placedSegments(workspace: Workspace): PlacedSegment[] {
  return [
    ...workspace.space_refs.map((placed) => ({
      segment_id: placed.segment_id,
      label: placed.label,
      kind: "space" as const,
    })),
    ...workspace.space.map((draft) => ({
      segment_id: draft.segment_id,
      label: draft.display_name,
      kind: "space" as const,
    })),
    ...workspace.ground_refs.map((placed) => ({
      segment_id: placed.segment_id,
      label: placed.label,
      kind: "ground" as const,
    })),
    ...workspace.ground.map((draft) => ({
      segment_id: draft.segment_id,
      label: draft.display_name,
      kind: "ground" as const,
    })),
  ];
}

/** Link interaction guidance. Persisted validation belongs to backend compile. */
export function linkWarnings(workspace: Workspace): string[] {
  const warnings: string[] = [];
  const placed = new Map(placedSegments(workspace).map((s) => [s.segment_id, s]));
  const seenIds = new Set<string>();
  for (const rule of workspace.links) {
    const id = emittedRuleId(rule);
    if (seenIds.has(id)) {
      warnings.push(`two link rules named "${id}" — rename one`);
    }
    seenIds.add(id);
    for (const endpoint of [rule.a, rule.b]) {
      if (!placed.has(endpoint.segment_id)) {
        warnings.push(
          `${rule.label || id}: segment "${endpoint.segment_id}" is no longer in the session`,
        );
      }
    }
    const a = placed.get(rule.a.segment_id);
    const b = placed.get(rule.b.segment_id);
    if (a?.kind === "ground" && b?.kind === "ground") {
      warnings.push(
        `${rule.label || id}: ground-to-ground links are terrestrial network territory — that arrives with routing, not link rules`,
      );
    }
  }
  return warnings;
}

/** The grammar id a link rule serializes under (boundaries key on it). */
export function emittedRuleId(rule: DraftLinkRule): string {
  return rule.rule_id;
}

export function emittedDomainId(domain: DraftRoutingDomain): string {
  return domain.domain_id;
}

/** Routing interaction guidance. Persisted validation belongs to backend compile. */
export function routingWarnings(workspace: Workspace): string[] {
  const warnings: string[] = [];
  const segments = placedSegments(workspace);
  const placed = new Set(segments.map((s) => s.segment_id));
  const domainIds = new Set<string>();
  for (const domain of workspace.routing_domains) {
    const id = emittedDomainId(domain);
    if (domainIds.has(id)) warnings.push(`two routing domains named "${id}" — rename one`);
    domainIds.add(id);
    if (domain.member_segment_ids.length === 0) {
      warnings.push(`${domain.label}: no member segments yet`);
    }
    for (const member of domain.member_segment_ids) {
      if (!placed.has(member)) {
        warnings.push(`${domain.label}: segment "${member}" is no longer in the session`);
      }
    }
    if (
      domain.hello_interval_s !== null &&
      domain.hold_interval_s !== null &&
      domain.hold_interval_s <= domain.hello_interval_s
    ) {
      warnings.push(`${domain.label}: hold must exceed hello`);
    }
  }
  const ruleById = new Map(workspace.links.map((rule) => [rule.rule_id, rule]));
  const draftDomainIds = new Set(workspace.routing_domains.map((d) => d.domain_id));
  for (const boundary of workspace.boundaries) {
    if (!ruleById.has(boundary.over_rule_id)) {
      warnings.push("a boundary rides a link rule that is no longer in the session");
    }
    if (
      !draftDomainIds.has(boundary.from_domain_id) ||
      !draftDomainIds.has(boundary.to_domain_id)
    ) {
      warnings.push("a boundary references a routing domain that no longer exists");
    } else if (boundary.from_domain_id === boundary.to_domain_id) {
      warnings.push("a boundary must exchange between two different domains");
    }
  }
  return warnings;
}

/** Reseed browser-only reference/member counters after structured recovery. */
export function reseedCounters(workspace: Workspace): void {
  const bump = (values: string[], prefix: string): number => {
    let max = 0;
    for (const value of values) {
      const match = value.match(new RegExp(`^${prefix}-(\\d+)$`));
      if (match) max = Math.max(max, Number(match[1]));
    }
    return max;
  };
  refCounter = Math.max(
    refCounter,
    bump(
      [...workspace.space_refs, ...workspace.ground_refs].map((r) => r.segment_id),
      "lib",
    ),
  );
  memberCounter = Math.max(
    memberCounter,
    bump(
      workspace.ground.flatMap((d) => d.members.map((m) => m.member_id)),
      "member",
    ),
  );
}
