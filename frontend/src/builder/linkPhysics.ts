// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Link physics derivation.
 *
 *  The resolved world already carries every node's terminal inventory
 *  (role, medium, elevation limits) — resolver truth, not a builder guess.
 *  Connecting two segments derives the rule's role, medium, masks, and
 *  topology from what the endpoints can physically form; the user owns
 *  every value from the seed onward. Combinations neither side can form
 *  are never offered as defaults and render disabled with the reason.
 *
 *  Topology defaults follow allocation reality: access schedules (all
 *  visible pairs); an intra-segment fabric is nearest-2; a cross-segment
 *  fixed link is nearest-1 (fixed links consume real terminal interfaces).
 */

import type { BuilderWorld, BuilderWorldNode } from "./builderTypes";
import {
  defaultLinkRule,
  placedSegments,
  LINK_MEDIA,
  type DraftLinkRule,
  type LinkMedium,
  type MountRole,
  type Workspace,
} from "./workspace";

export interface SegmentCapability {
  /** "role|medium" tokens present on at least one node of the segment. */
  pairs: Set<string>;
  /** Strictest access-terminal elevation floor seen on the segment. */
  access_min_elevation_deg: number | null;
}

/** The elevation floor a node's access beam is drawn with. Declared floors
 *  win (strictest when several blocks declare one — the same reading as
 *  capabilitiesBySegment); an access terminal with no declared floor serves
 *  to the geometric horizon (0). No access terminal, no beam: null. */
export function accessBeamElevationDeg(node: BuilderWorldNode): number | null {
  let floor: number | null = null;
  let hasAccess = false;
  for (const block of node.terminal_inventory) {
    if (block.endpoint_role !== "access") continue;
    hasAccess = true;
    if (block.min_elevation_deg !== null) {
      floor = Math.max(floor ?? 0, block.min_elevation_deg);
    }
  }
  if (!hasAccess) return null;
  return floor ?? 0;
}

/** Collect each segment's terminal capability from the resolved world. */
export function capabilitiesBySegment(
  world: BuilderWorld | null,
): Map<string, SegmentCapability> {
  const capabilities = new Map<string, SegmentCapability>();
  if (!world) return capabilities;
  for (const node of world.nodes) {
    let capability = capabilities.get(node.segment_id);
    if (!capability) {
      capability = { pairs: new Set(), access_min_elevation_deg: null };
      capabilities.set(node.segment_id, capability);
    }
    for (const block of node.terminal_inventory) {
      capability.pairs.add(`${block.endpoint_role}|${block.medium}`);
      if (block.endpoint_role === "access" && block.min_elevation_deg !== null) {
        capability.access_min_elevation_deg = Math.max(
          capability.access_min_elevation_deg ?? 0,
          block.min_elevation_deg,
        );
      }
    }
  }
  return capabilities;
}

export type LinkRole = MountRole;

/** True when both endpoints carry at least one mount of role|medium. */
export function canForm(
  a: SegmentCapability | undefined,
  b: SegmentCapability | undefined,
  role: LinkRole,
  medium: LinkMedium,
): boolean {
  const token = `${role}|${medium}`;
  return Boolean(a?.pairs.has(token) && b?.pairs.has(token));
}

export interface DerivedPhysics {
  role: LinkRole;
  medium: LinkMedium;
  /** Mask for the ground endpoint (access rules), from its own terminals. */
  ground_mask_deg: number | null;
  topology_mode: "visible_candidates" | "nearest_n";
  topology_n: number;
  /** True when derivation found a formable combination. */
  formable: boolean;
}

// Derivation preference among the grammar's media — derived from the owned
// vocabulary, never re-listed: the Record is exhaustive over LinkMedium, so
// adding a medium to the grammar fails to compile here instead of silently
// never being offered.
const MEDIUM_RANK: Record<LinkMedium, number> = { optical: 0, rf: 1 };
const MEDIUM_ORDER: readonly LinkMedium[] = [...LINK_MEDIA].sort(
  (a, b) => MEDIUM_RANK[a] - MEDIUM_RANK[b],
);

/** The ground-access elevation mask seeded when NO terminal declares a floor —
 *  a default the user then owns, never a derived or measured value. The number
 *  is a seed, not physics; both the connect/re-derive notice and the editor say
 *  so with the exact same words. */
export const DEFAULT_GROUND_MASK_DEG = 25;
export const SEEDED_GROUND_MASK_NOTE = `no terminal declares an elevation floor — seeded default ${DEFAULT_GROUND_MASK_DEG}°`;

/** The seed note for an access endpoint whose ground mask is the seeded default
 *  (role access, ground side, value equals the default, and the segment declares
 *  no floor) — or null when the mask is a declared floor or the endpoint is not
 *  a seeded ground access. One source for both carriers. */
export function groundMaskSeedNote(
  capability: SegmentCapability | undefined,
  endpoint: { role: string; kind: string; min_elevation_deg: number | null },
): string | null {
  const declared = capability?.access_min_elevation_deg ?? null;
  return endpoint.role === "access" &&
    endpoint.kind === "ground" &&
    declared === null &&
    endpoint.min_elevation_deg === DEFAULT_GROUND_MASK_DEG
    ? SEEDED_GROUND_MASK_NOTE
    : null;
}

/** Derive the physics for a pair of segments. Preference order encodes the
 *  role semantics: same segment = fabric (isl), space to space =
 *  crosslink, anything with ground = access. Falls back through formable
 *  combinations; when nothing is formable, returns the semantic default
 *  with formable=false (the wall will say why — never silently invent). */
export function deriveLinkPhysics(
  capabilities: Map<string, SegmentCapability>,
  a: { segment_id: string; kind: "space" | "ground" },
  b: { segment_id: string; kind: "space" | "ground" },
): DerivedPhysics {
  const capA = capabilities.get(a.segment_id);
  const capB = capabilities.get(b.segment_id);
  const groundSide = a.kind === "ground" ? capA : b.kind === "ground" ? capB : null;

  const preferences: { role: LinkRole; mode: DerivedPhysics["topology_mode"]; n: number }[] =
    a.segment_id === b.segment_id
      ? [
          { role: "isl", mode: "nearest_n", n: 2 },
          { role: "crosslink", mode: "nearest_n", n: 2 },
        ]
      : a.kind === "space" && b.kind === "space"
        ? [
            { role: "crosslink", mode: "nearest_n", n: 1 },
            { role: "isl", mode: "nearest_n", n: 1 },
          ]
        : [{ role: "access", mode: "visible_candidates", n: 1 }];

  for (const preference of preferences) {
    for (const medium of MEDIUM_ORDER) {
      if (canForm(capA, capB, preference.role, medium)) {
        return {
          role: preference.role,
          medium,
          ground_mask_deg: groundSide?.access_min_elevation_deg ?? null,
          topology_mode: preference.mode,
          topology_n: preference.n,
          formable: true,
        };
      }
    }
  }
  const fallback = preferences[0] as { role: LinkRole; mode: DerivedPhysics["topology_mode"]; n: number };
  return {
    role: fallback.role,
    // The role's fallback medium, from the owned order — never re-listed
    // literals. MEDIUM_ORDER is sorted ascending by rank (optical, rf), so an
    // access link falls back to the highest-rank medium (radio, the ground
    // default) and a fabric to the lowest (optical). A grammar medium change
    // flows through MEDIUM_RANK, the one source.
    medium:
      fallback.role === "access"
        ? MEDIUM_ORDER[MEDIUM_ORDER.length - 1]!
        : MEDIUM_ORDER[0]!,
    ground_mask_deg: groundSide?.access_min_elevation_deg ?? null,
    topology_mode: fallback.mode,
    topology_n: fallback.n,
    formable: false,
  };
}

/** Connect two placed segments: both endpoints are known before the rule
 *  exists, so the seed is computed from truth once — nothing to re-point.
 *  Falls back to kind-based defaults when the world hasn't resolved. */
export function connectSegments(
  workspace: Workspace,
  world: BuilderWorld | null,
  fromId: string,
  toId: string,
): DraftLinkRule {
  const placed = placedSegments(workspace);
  const from = placed.find((s) => s.segment_id === fromId);
  const to = placed.find((s) => s.segment_id === toId);
  if (!from || !to) throw new Error("connect endpoints must be placed segments");
  const rule = defaultLinkRule(from, to, workspace.links);
  const physics = deriveLinkPhysics(capabilitiesBySegment(world), from, to);
  const kindOf = new Map(placed.map((s) => [s.segment_id, s.kind]));
  const maskFor = (segmentId: string) =>
    physics.role === "access" && kindOf.get(segmentId) === "ground"
      ? (physics.ground_mask_deg ?? DEFAULT_GROUND_MASK_DEG)
      : null;
  rule.a = {
    ...rule.a,
    role: physics.role,
    medium: physics.medium,
    min_elevation_deg: maskFor(rule.a.segment_id),
  };
  rule.b = {
    ...rule.b,
    role: physics.role,
    medium: physics.medium,
    min_elevation_deg: maskFor(rule.b.segment_id),
  };
  rule.topology_mode = physics.topology_mode;
  rule.topology_n = physics.topology_n;
  return rule;
}

/** re-derive a rule's physics after an endpoint re-point — loudly.
 *  Returns the patch plus a human sentence for the notice. */
export function rederiveRule(
  workspace: Workspace,
  world: BuilderWorld | null,
  rule: DraftLinkRule,
  side: "a" | "b",
  newSegmentId: string,
): { patch: Partial<DraftLinkRule>; notice: string } {
  const placed = placedSegments(workspace);
  const aId = side === "a" ? newSegmentId : rule.a.segment_id;
  const bId = side === "b" ? newSegmentId : rule.b.segment_id;
  const a = placed.find((s) => s.segment_id === aId);
  const b = placed.find((s) => s.segment_id === bId);
  if (!a || !b) {
    return {
      patch: { [side]: { ...rule[side], segment_id: newSegmentId } },
      notice: "endpoint changed — pick a placed segment to re-derive physics",
    };
  }
  const physics = deriveLinkPhysics(capabilitiesBySegment(world), a, b);
  const maskFor = (segment: { kind: string }) =>
    physics.role === "access" && segment.kind === "ground"
      ? (physics.ground_mask_deg ?? DEFAULT_GROUND_MASK_DEG)
      : null;
  const patch: Partial<DraftLinkRule> = {
    a: {
      ...rule.a,
      segment_id: aId,
      role: physics.role,
      medium: physics.medium,
      min_elevation_deg: maskFor(a),
    },
    b: {
      ...rule.b,
      segment_id: bId,
      role: physics.role,
      medium: physics.medium,
      min_elevation_deg: maskFor(b),
    },
    topology_mode: physics.topology_mode,
    topology_n: physics.topology_n,
  };
  // The mask fragment names a SEEDED default distinctly from a declared floor:
  // a re-derive that seeded 25° because no terminal declares one must never
  // read as a measured/derived value.
  const groundSided = physics.role === "access" && (a.kind === "ground" || b.kind === "ground");
  const mask = !groundSided
    ? ""
    : physics.ground_mask_deg === null
      ? ` · ${SEEDED_GROUND_MASK_NOTE}`
      : ` · ${physics.ground_mask_deg}° mask`;
  const topology =
    physics.topology_mode === "nearest_n" ? ` · nearest-${physics.topology_n}` : " · all visible pairs";
  return {
    patch,
    notice: `re-derived: ${physics.role} · ${physics.medium}${mask}${topology}${
      physics.formable ? "" : " — WARNING: neither side has matching terminals"
    }`,
  };
}
