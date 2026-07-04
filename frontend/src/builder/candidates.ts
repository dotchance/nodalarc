// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Candidate-pair preview for resolved link rules, at the session epoch.
 *
 *  The canvas may only draw geometrically possible paths: a rule renders as
 *  its line-of-sight candidate pairs at this instant — straight lines between
 *  members that can actually see each other — and nothing where geometry
 *  forbids. Decisions run in physical km space (positions from the same
 *  client propagation the globe uses); rendering happens elsewhere in render
 *  space. OME remains the runtime authority; this is the preview of the same
 *  geometric tests.
 *
 *  Previews are honest about scope: rules whose geometry this preview cannot
 *  compute yet (terrestrial surface runs, inter-body spans) carry an explicit
 *  note instead of fake lines, and any cap is reported, never silent.
 *
 *  Fixed rules (nearest_n, explicit_pairs) draw the allocator's own pairs,
 *  shipped in the world as link_candidates — the preview never re-derives
 *  fixed selection or interface capacity (a client-side budget once
 *  double-counted mounts across medium selectors; capacity truth is
 *  computed once, server-side). Each allocated pair is still geometry-gated
 *  before drawing: LOS, min-of-pair terminal range, field of regard — an
 *  allocated pair that is not feasible at this instant is counted in the
 *  rule note, never drawn. Visibility rules (visible_candidates) remain a
 *  pure geometric preview of runtime scheduling. Tracking-rate gating
 *  remains runtime-only.
 */

import {
  bodyMathFromFrame,
  kmPerRenderUnitFromEphemeris,
  propagateNode,
} from "../sim/ephemeris";
import {
  distanceKm,
  elevationDeg,
  geodeticToBodyFixedKm,
  segmentIntersectsBody,
  type Vec3Km,
} from "../sim/lineOfSight";
import type { BuilderLinkEndpoint, BuilderLinkRule, BuilderWorld } from "./builderTypes";

// Compute/render bounds — reported in the rule preview whenever they bite.
const MAX_TESTED_PAIRS_PER_RULE = 40000;
const MAX_RENDERED_PAIRS_PER_RULE = 1200;

export interface CandidatePair {
  rule_id: string;
  kind: string;
  a: string;
  b: string;
  range_km: number;
}

export interface RulePreview {
  rule_id: string;
  kind: string;
  mode: string;
  enabled: boolean;
  candidates: number;
  note: string | null;
}

interface NodeGeometry {
  body: string;
  kind: string;
  latDeg: number;
  lonDeg: number;
  positionKm: Vec3Km;
  bodyMeanRadiusKm: number;
}

function nodeGeometries(world: BuilderWorld): Map<string, NodeGeometry> {
  const kmPerRenderUnit = kmPerRenderUnitFromEphemeris(world.ephemeris);
  const out = new Map<string, NodeGeometry>();
  for (const node of world.nodes) {
    if (node.kind === "satellite") {
      const entry = world.ephemeris.nodes[node.node_id];
      if (!entry || entry.type === "tle") continue;
      const frame = world.ephemeris.body_frames[entry.reference_body];
      if (!frame) continue;
      const body = bodyMathFromFrame(frame, kmPerRenderUnit);
      const pos = propagateNode(entry, world.epoch_unix, world.epoch_unix, body);
      out.set(node.node_id, {
        body: entry.reference_body,
        kind: node.kind,
        latDeg: pos.latDeg,
        lonDeg: pos.lonDeg,
        positionKm: geodeticToBodyFixedKm(pos.latDeg, pos.lonDeg, pos.altKm, body),
        bodyMeanRadiusKm: frame.mean_radius_km,
      });
    } else if (node.surface_position) {
      const surface = node.surface_position;
      const frame = world.ephemeris.body_frames[surface.body];
      if (!frame) continue;
      const body = bodyMathFromFrame(frame, kmPerRenderUnit);
      out.set(node.node_id, {
        body: surface.body,
        kind: node.kind,
        latDeg: surface.lat_deg,
        lonDeg: surface.lon_deg,
        positionKm: geodeticToBodyFixedKm(
          surface.lat_deg,
          surface.lon_deg,
          surface.alt_m / 1000,
          body,
        ),
        bodyMeanRadiusKm: frame.mean_radius_km,
      });
    }
  }
  return out;
}

/** Per-node terminal facts for one rule endpoint: the most permissive
 *  matching terminal's range and field of regard (null = ungated). */
interface NodeTerminalFacts {
  maxRangeKm: number | null;
  fieldOfRegardDeg: number | null;
}

function endpointTerminalFacts(
  world: BuilderWorld,
  endpoint: BuilderLinkEndpoint,
): Map<string, NodeTerminalFacts> {
  const members = new Set(endpoint.node_ids);
  const facts = new Map<string, NodeTerminalFacts>();
  for (const node of world.nodes) {
    if (!members.has(node.node_id)) continue;
    let maxRangeKm: number | null = null;
    let unlimited = false;
    let fieldOfRegardDeg: number | null = null;
    let anyFieldUnknown = false;
    for (const block of node.terminal_inventory) {
      if (block.endpoint_role !== endpoint.terminal_role) continue;
      if (endpoint.terminal_medium !== null && block.medium !== endpoint.terminal_medium) {
        continue;
      }
      if (block.max_range_km === null) unlimited = true;
      else maxRangeKm = Math.max(maxRangeKm ?? 0, block.max_range_km);
      // Most permissive matching block, same stance as range above; a block
      // with no declared field of regard leaves the node ungated.
      if (block.field_of_regard_deg === null) anyFieldUnknown = true;
      else fieldOfRegardDeg = Math.max(fieldOfRegardDeg ?? 0, block.field_of_regard_deg);
    }
    facts.set(node.node_id, {
      maxRangeKm: unlimited ? null : maxRangeKm,
      fieldOfRegardDeg: anyFieldUnknown ? null : fieldOfRegardDeg,
    });
  }
  return facts;
}

/** Angle between the line of sight and the local horizontal plane at
 *  `from` (radians) — the runtime engine's pointing measure. Positions are
 *  body-fixed km, origin at the body center. */
function offLocalHorizontalRad(from: Vec3Km, to: Vec3Km): number {
  const los: Vec3Km = [to[0] - from[0], to[1] - from[1], to[2] - from[2]];
  const losMag = Math.hypot(los[0], los[1], los[2]);
  const radialMag = Math.hypot(from[0], from[1], from[2]);
  if (losMag < 1e-10 || radialMag < 1e-10) return 0;
  const cosZenith = Math.max(
    -1,
    Math.min(1, (los[0] * from[0] + los[1] * from[1] + los[2] * from[2]) / (losMag * radialMag)),
  );
  return Math.abs(Math.acos(cosZenith) - Math.PI / 2);
}

function pairPasses(
  rule: BuilderLinkRule,
  a: NodeGeometry,
  b: NodeGeometry,
  terminalRangeKm: number | null,
  fieldOfRegardDeg: number | null,
): { ok: boolean; rangeKm: number; beyondTerminal: boolean; outsideRegard: boolean } {
  const rangeKm = distanceKm(a.positionKm, b.positionKm);
  // The runtime applies min(both terminals' range); the preview mirrors it.
  if (terminalRangeKm !== null && rangeKm > terminalRangeKm) {
    return { ok: false, rangeKm, beyondTerminal: true, outsideRegard: false };
  }
  if (rule.max_range_km !== null && rangeKm > rule.max_range_km) {
    return { ok: false, rangeKm, beyondTerminal: false, outsideRegard: false };
  }
  if (segmentIntersectsBody(a.positionKm, b.positionKm, a.bodyMeanRadiusKm)) {
    return { ok: false, rangeKm, beyondTerminal: false, outsideRegard: false };
  }
  // Satellite pairs: the runtime's field-of-regard cone, centered on the
  // local horizontal, min of both sides. 360 or undeclared = unrestricted.
  if (
    fieldOfRegardDeg !== null &&
    fieldOfRegardDeg < 360 &&
    a.kind === "satellite" &&
    b.kind === "satellite"
  ) {
    const halfAngle = (fieldOfRegardDeg / 2) * (Math.PI / 180);
    if (
      offLocalHorizontalRad(a.positionKm, b.positionKm) > halfAngle ||
      offLocalHorizontalRad(b.positionKm, a.positionKm) > halfAngle
    ) {
      return { ok: false, rangeKm, beyondTerminal: false, outsideRegard: true };
    }
  }
  const [endA, endB] = rule.endpoints;
  if (endA.min_elevation_deg !== null && a.kind !== "satellite") {
    if (elevationDeg(a.latDeg, a.lonDeg, a.positionKm, b.positionKm) < endA.min_elevation_deg) {
      return { ok: false, rangeKm, beyondTerminal: false, outsideRegard: false };
    }
  }
  if (endB.min_elevation_deg !== null && b.kind !== "satellite") {
    if (elevationDeg(b.latDeg, b.lonDeg, b.positionKm, a.positionKm) < endB.min_elevation_deg) {
      return { ok: false, rangeKm, beyondTerminal: false, outsideRegard: false };
    }
  }
  return { ok: true, rangeKm, beyondTerminal: false, outsideRegard: false };
}

export interface CandidateComputation {
  pairs: CandidatePair[];
  previews: RulePreview[];
}

export function computeCandidates(world: BuilderWorld): CandidateComputation {
  const geometry = nodeGeometries(world);
  const pairs: CandidatePair[] = [];
  const previews: RulePreview[] = [];
  // Fixed pairs come from the server's allocator, grouped once.
  const allocatedByRule = new Map<string, { a: string; b: string }[]>();
  for (const candidate of world.link_candidates) {
    const list = allocatedByRule.get(candidate.rule_id) ?? [];
    list.push({ a: candidate.node_a, b: candidate.node_b });
    allocatedByRule.set(candidate.rule_id, list);
  }

  for (const rule of world.link_rules) {
    const preview: RulePreview = {
      rule_id: rule.rule_id,
      kind: rule.kind,
      mode: rule.topology_mode,
      enabled: rule.enabled,
      candidates: 0,
      note: null,
    };
    previews.push(preview);
    if (!rule.enabled) {
      preview.note = "rule disabled";
      continue;
    }

    const [endA, endB] = rule.endpoints;
    const factsA = endpointTerminalFacts(world, endA);
    const factsB = endpointTerminalFacts(world, endB);
    const pairTerminalRange = (aId: string, bId: string): number | null => {
      const ra = factsA.get(aId)?.maxRangeKm ?? null;
      const rb = factsB.get(bId)?.maxRangeKm ?? null;
      if (ra === null) return rb;
      if (rb === null) return ra;
      return Math.min(ra, rb);
    };
    // The runtime applies min(both terminals' field of regard).
    const pairFieldOfRegard = (aId: string, bId: string): number | null => {
      const fa = factsA.get(aId)?.fieldOfRegardDeg ?? null;
      const fb = factsB.get(bId)?.fieldOfRegardDeg ?? null;
      if (fa === null) return fb;
      if (fb === null) return fa;
      return Math.min(fa, fb);
    };
    const geomA = endA.node_ids.map((id) => [id, geometry.get(id)] as const);
    const geomB = endB.node_ids.map((id) => [id, geometry.get(id)] as const);
    const probeA = geomA.find(([, g]) => g)?.[1];
    const probeB = geomB.find(([, g]) => g)?.[1];
    if (!probeA || !probeB) {
      preview.note = "no computable geometry for this rule's members";
      continue;
    }
    if (probeA.body !== probeB.body) {
      preview.note = "inter-body span — preview pending, runtime computes contacts";
      continue;
    }
    if (probeA.kind !== "satellite" && probeB.kind !== "satellite") {
      preview.note = "terrestrial run — surface routing preview pending";
      continue;
    }

    const geometricPairs: CandidatePair[] = [];
    let tested = 0;
    let truncated = false;
    let beyondTerminalRange = 0;
    let outsideFieldOfRegard = 0;
    let fixedInfeasible = 0;
    const consider = (aId: string, aGeom: NodeGeometry, bId: string, bGeom: NodeGeometry) => {
      if (tested >= MAX_TESTED_PAIRS_PER_RULE) {
        truncated = true;
        return;
      }
      tested += 1;
      const { ok, rangeKm, beyondTerminal, outsideRegard } = pairPasses(
        rule,
        aGeom,
        bGeom,
        pairTerminalRange(aId, bId),
        pairFieldOfRegard(aId, bId),
      );
      if (beyondTerminal) beyondTerminalRange += 1;
      if (outsideRegard) outsideFieldOfRegard += 1;
      if (ok) {
        geometricPairs.push({ rule_id: rule.rule_id, kind: rule.kind, a: aId, b: bId, range_km: rangeKm });
      }
    };

    const allocated = allocatedByRule.get(rule.rule_id);
    const fixedMode =
      rule.topology_mode === "explicit_pairs" || rule.topology_mode === "nearest_n";
    let fixedUnallocated = false;
    if (fixedMode) {
      // Fixed rules: the allocator already chose the pairs and the
      // interfaces — draw its selection, geometry-gated for this instant.
      // Re-deriving nearest-N client-side was a second allocator.
      // The wire pair is lexicographically canonical, NOT endpoint-ordered:
      // orient each pair to the rule's endpoints by membership, so per-side
      // terminal facts and elevation masks land on the right node.
      const membersA = new Set(endA.node_ids);
      const membersB = new Set(endB.node_ids);
      let infeasibleNow = 0;
      for (const pair of allocated ?? []) {
        const straight = membersA.has(pair.a) && membersB.has(pair.b);
        const a = straight ? pair.a : pair.b;
        const b = straight ? pair.b : pair.a;
        const aGeom = geometry.get(a);
        const bGeom = geometry.get(b);
        if (!aGeom || !bGeom) continue;
        const { ok, rangeKm } = pairPasses(
          rule,
          aGeom,
          bGeom,
          pairTerminalRange(a, b),
          pairFieldOfRegard(a, b),
        );
        if (ok) {
          geometricPairs.push({ rule_id: rule.rule_id, kind: rule.kind, a, b, range_km: rangeKm });
        } else {
          infeasibleNow += 1;
        }
      }
      if (infeasibleNow > 0) {
        fixedInfeasible = infeasibleNow;
      }
      fixedUnallocated = (allocated ?? []).length === 0;
    } else {
      // visible_candidates: every geometrically possible member pair.
      const sameSet = endA.node_ids === endB.node_ids ||
        (endA.segment_id === endB.segment_id && endA.node_ids.length === endB.node_ids.length);
      for (let i = 0; i < geomA.length; i++) {
        const [aId, aGeom] = geomA[i]!;
        if (!aGeom) continue;
        const start = sameSet ? i + 1 : 0;
        for (let j = start; j < geomB.length; j++) {
          const [bId, bGeom] = geomB[j]!;
          if (!bGeom || aId === bId) continue;
          consider(aId, aGeom, bId, bGeom);
        }
      }
    }

    geometricPairs.sort((x, y) => x.range_km - y.range_km);
    const rulePairs: CandidatePair[] = geometricPairs;

    const notes: string[] = [];
    if (rulePairs.length > MAX_RENDERED_PAIRS_PER_RULE) {
      notes.push(`showing ${MAX_RENDERED_PAIRS_PER_RULE} nearest of ${rulePairs.length} candidates`);
      rulePairs.length = MAX_RENDERED_PAIRS_PER_RULE;
    }
    if (truncated) notes.push(`pair budget hit — tested ${tested} pairs`);
    if (beyondTerminalRange > 0) {
      notes.push(`${beyondTerminalRange} pairs beyond terminal range`);
    }
    if (outsideFieldOfRegard > 0) {
      notes.push(`${outsideFieldOfRegard} pairs outside field of regard`);
    }
    if (fixedInfeasible > 0) {
      notes.push(
        `${fixedInfeasible} allocated pair${fixedInfeasible === 1 ? "" : "s"} not feasible at this instant`,
      );
    }
    if (fixedUnallocated) {
      // Geometry was never consulted — saying "geometry forbids" here would
      // contradict the rule editor's "allocator: 0 pairs" on the same screen.
      notes.push("the allocator granted no pairs for this rule");
    }
    if (rulePairs.length === 0 && notes.length === 0) {
      notes.push("rule permits, geometry currently forbids — runtime computes contacts over time");
    }
    preview.note = notes.length ? notes.join("; ") : null;
    preview.candidates = rulePairs.length;
    pairs.push(...rulePairs);
  }

  return { pairs, previews };
}
