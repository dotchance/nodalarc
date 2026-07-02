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
import type { BuilderLinkRule, BuilderWorld } from "./builderTypes";

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

function pairPasses(
  rule: BuilderLinkRule,
  a: NodeGeometry,
  b: NodeGeometry,
): { ok: boolean; rangeKm: number } {
  const rangeKm = distanceKm(a.positionKm, b.positionKm);
  if (rule.max_range_km !== null && rangeKm > rule.max_range_km) {
    return { ok: false, rangeKm };
  }
  if (segmentIntersectsBody(a.positionKm, b.positionKm, a.bodyMeanRadiusKm)) {
    return { ok: false, rangeKm };
  }
  const [endA, endB] = rule.endpoints;
  if (endA.min_elevation_deg !== null && a.kind !== "satellite") {
    if (elevationDeg(a.latDeg, a.lonDeg, a.positionKm, b.positionKm) < endA.min_elevation_deg) {
      return { ok: false, rangeKm };
    }
  }
  if (endB.min_elevation_deg !== null && b.kind !== "satellite") {
    if (elevationDeg(b.latDeg, b.lonDeg, b.positionKm, a.positionKm) < endB.min_elevation_deg) {
      return { ok: false, rangeKm };
    }
  }
  return { ok: true, rangeKm };
}

export interface CandidateComputation {
  pairs: CandidatePair[];
  previews: RulePreview[];
}

export function computeCandidates(world: BuilderWorld): CandidateComputation {
  const geometry = nodeGeometries(world);
  const pairs: CandidatePair[] = [];
  const previews: RulePreview[] = [];

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

    const rulePairs: CandidatePair[] = [];
    let tested = 0;
    let truncated = false;
    const consider = (aId: string, aGeom: NodeGeometry, bId: string, bGeom: NodeGeometry) => {
      if (tested >= MAX_TESTED_PAIRS_PER_RULE) {
        truncated = true;
        return;
      }
      tested += 1;
      const { ok, rangeKm } = pairPasses(rule, aGeom, bGeom);
      if (ok) {
        rulePairs.push({ rule_id: rule.rule_id, kind: rule.kind, a: aId, b: bId, range_km: rangeKm });
      }
    };

    if (rule.topology_mode === "explicit_pairs") {
      for (const [a, b] of rule.explicit_pairs) {
        const aGeom = geometry.get(a);
        const bGeom = geometry.get(b);
        if (aGeom && bGeom) consider(a, aGeom, b, bGeom);
      }
    } else if (rule.topology_mode === "nearest_n" || rule.topology_mode === "nearest_visible") {
      // Convention: endpoint[0] is the selecting side (rule-endpoint asymmetry
      // is a registered grammar question; the preview follows OME's ordering).
      const n = rule.topology_mode === "nearest_visible" ? 1 : (rule.topology_n ?? 1);
      for (const [aId, aGeom] of geomA) {
        if (!aGeom) continue;
        const reachable: CandidatePair[] = [];
        for (const [bId, bGeom] of geomB) {
          if (!bGeom || aId === bId) continue;
          if (tested >= MAX_TESTED_PAIRS_PER_RULE) {
            truncated = true;
            break;
          }
          tested += 1;
          const { ok, rangeKm } = pairPasses(rule, aGeom, bGeom);
          if (ok) {
            reachable.push({ rule_id: rule.rule_id, kind: rule.kind, a: aId, b: bId, range_km: rangeKm });
          }
        }
        reachable.sort((x, y) => x.range_km - y.range_km);
        rulePairs.push(...reachable.slice(0, n));
      }
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

    if (rulePairs.length > MAX_RENDERED_PAIRS_PER_RULE) {
      rulePairs.sort((x, y) => x.range_km - y.range_km);
      preview.note = `showing ${MAX_RENDERED_PAIRS_PER_RULE} nearest of ${rulePairs.length} candidates`;
      rulePairs.length = MAX_RENDERED_PAIRS_PER_RULE;
    } else if (truncated) {
      preview.note = `pair budget hit — tested ${tested} pairs`;
    } else if (rulePairs.length === 0) {
      preview.note = "rule permits, geometry currently forbids — runtime computes contacts over time";
    }
    preview.candidates = rulePairs.length;
    pairs.push(...rulePairs);
  }

  return { pairs, previews };
}
