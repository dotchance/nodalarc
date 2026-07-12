// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Interaction helpers over the backend-owned visual Builder DTO. */

import type {
  BuilderVisualGroundSiteIntent,
  BuilderVisualWorkspace,
  BuilderVisualLinkEndpoint,
  BuilderVisualNode,
  BuilderVisualGroundBoresight,
  BuilderVisualRoutingBoundary,
  BuilderVisualRoutingDomain,
  BuilderVisualSpaceDraft,
  BuilderVisualTerminalMount,
} from "./generated/builderApi";

/** Mutable materialization of a Pydantic response model.
 *
 * FastAPI serializes every Pydantic default, so defaulted response properties
 * are present even though the shared request/response TypeScript contract marks
 * them optional. Explicit null remains explicit authoring state.
 */
export type MaterializedMutable<T> =
  T extends ReadonlyArray<infer Item>
    ? MaterializedMutable<Item>[]
    : T extends object
      ? {
          -readonly [Key in keyof T]-?: MaterializedMutable<Exclude<T[Key], undefined>>;
        }
      : T;

export type Workspace = MaterializedMutable<BuilderVisualWorkspace>;
export type DraftConstellation = Workspace["space"][number];
export type DraftOrbit = DraftConstellation["orbit"];
export type DraftNode = Exclude<DraftConstellation["node_draft"], null>;
export type DraftTerminalMount = DraftNode["terminals"][number];
export type DraftGroundSet = Workspace["ground"][number];
export type DraftGroundSite = DraftGroundSet["members"][number];
export type DraftSiteObject = Exclude<DraftGroundSite["site"], null>;
export type DraftSiteNode = DraftSiteObject["nodes"][number];
export type DraftLinkRule = Workspace["links"][number];
export type DraftLinkEndpoint = DraftLinkRule["a"];
export type DraftRoutingDomain = Workspace["routing_domains"][number];
export type DraftBoundary = Workspace["boundaries"][number];
export type ParsedSiteLine = BuilderVisualGroundSiteIntent;

/** UI labels over generated visual-authoring application types. */
export type MountRole = NonNullable<BuilderVisualTerminalMount["role"]>;
export type LinkMedium = NonNullable<BuilderVisualLinkEndpoint["medium"]>;
export type Protocol = NonNullable<BuilderVisualRoutingDomain["protocol"]>;
export type Adapter = NonNullable<BuilderVisualRoutingBoundary["adapter"]>;
export type Forwarding = NonNullable<BuilderVisualNode["forwarding"]>;
export type PhasingMode = BuilderVisualSpaceDraft["phasing_mode"];
export type GroundBoresight = BuilderVisualGroundBoresight;

function memberSiteId(member: DraftGroundSite): string {
  return member.kind === "draft" && member.site ? member.site.site_id : member.site_id;
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
    const alt = parts.length > 3 ? Number(parts[3]) : undefined;
    if (
      !Number.isFinite(lat) ||
      !Number.isFinite(lon) ||
      (alt !== undefined && !Number.isFinite(alt))
    ) {
      errors.push(`"${line}" — lat, lon, alt must be numbers`);
      continue;
    }
    if (!name) {
      errors.push(`"${line}" — name is required`);
      continue;
    }
    rows.push({
      name,
      lat_deg: lat,
      lon_deg: lon,
      ...(alt === undefined ? {} : { alt_m: alt }),
    });
  }
  return { rows, errors };
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
    if (site.lat_deg !== null && Math.abs(site.lat_deg) > 90) {
      warnings.push(`${site.display_name}: latitude ${site.lat_deg} is off the map (±90)`);
    }
    if (site.lon_deg !== null && Math.abs(site.lon_deg) > 180) {
      warnings.push(`${site.display_name}: longitude ${site.lon_deg} is off the map (±180)`);
    }
  }
  return warnings;
}

/** Every placed segment available to interaction guidance. */
interface PlacedSegment {
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
