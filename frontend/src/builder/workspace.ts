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
