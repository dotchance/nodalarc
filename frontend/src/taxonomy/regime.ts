// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Orbital-regime taxonomy: the common visual identity for constellation
 * classes. Regime is an INTRINSIC property of a node's authored orbit — a
 * Molniya bird at perigee is still HEO — so classification reads the mean
 * elements from the session ephemeris (a static fact per epoch), never the
 * instantaneous position. Nothing here is heuristic: every rule is a
 * deterministic function of authored elements, and anything outside the
 * known classes reports "unknown" rather than guessing.
 *
 * Tints live in tokens (taxonomy slot — identical in both themes). Regime
 * color identifies object class; it must never be reused for health/state.
 */

import { tokens } from "../styles/tokens";
import type { EphemerisNode, SessionEphemeris } from "../sim/ephemeris";

export type Regime = "leo" | "meo" | "geo" | "heo" | "luna" | "unknown";

export const REGIMES: readonly Regime[] = ["leo", "meo", "geo", "heo", "luna", "unknown"];

interface RegimeTint {
  css: string;
  hex: number;
  label: string;
}

function tint(css: string, label: string): RegimeTint {
  return { css, hex: parseInt(css.replace("#", ""), 16), label };
}

export const REGIME_TINT: Record<Regime, RegimeTint> = {
  leo: tint(tokens.regimeLeo, "LEO"),
  meo: tint(tokens.regimeMeo, "MEO"),
  geo: tint(tokens.regimeGeo, "GEO"),
  heo: tint(tokens.regimeHeo, "HEO"),
  luna: tint(tokens.regimeLuna, "Lunar"),
  unknown: tint("#" + tokens.colorNodeUnknown.toString(16).padStart(6, "0"), "Unclassified"),
};

/** Geostationary altitude (km) and the band treated as GEO-class. */
const GEO_ALTITUDE_KM = 35_786;
const GEO_BAND_KM = 1_500;
/** LEO/MEO boundary (km above the body surface), the standard 2000 km line. */
const LEO_CEILING_KM = 2_000;
/** Eccentricity at or above which an Earth orbit is highly-elliptical class. */
const HEO_ECCENTRICITY = 0.25;

/** Classify one ephemeris node from its authored elements. Pure. */
export function classifyRegime(node: EphemerisNode, bodyMeanRadiusKm: number | undefined): Regime {
  if (node.reference_body === "luna") return "luna";
  if (node.reference_body !== "earth") return "unknown";
  if (node.type !== "keplerian") return "unknown"; // tle is runtime-future; fixed is ground
  if (bodyMeanRadiusKm === undefined) return "unknown";
  if (node.eccentricity >= HEO_ECCENTRICITY) return "heo";
  const meanAltitudeKm = node.semi_major_axis_km - bodyMeanRadiusKm;
  if (meanAltitudeKm < LEO_CEILING_KM) return "leo";
  if (meanAltitudeKm < GEO_ALTITUDE_KM - GEO_BAND_KM) return "meo";
  if (meanAltitudeKm <= GEO_ALTITUDE_KM + GEO_BAND_KM) return "geo";
  return "unknown";
}

/** Regime per orbiting node id for one ephemeris epoch (ground nodes are not
 *  in the index — surface identity is the body, not an orbit class). */
export function buildRegimeIndex(ephemeris: SessionEphemeris | null): ReadonlyMap<string, Regime> {
  const index = new Map<string, Regime>();
  if (!ephemeris) return index;
  for (const [nodeId, node] of Object.entries(ephemeris.nodes)) {
    if (node.type === "fixed") continue;
    const body = ephemeris.body_frames[node.reference_body];
    index.set(nodeId, classifyRegime(node, body?.mean_radius_km));
  }
  return index;
}
