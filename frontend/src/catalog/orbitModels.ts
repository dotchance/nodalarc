// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import type { ConstellationPreset, OrbitPropagator } from "./wizardTypes";

export interface OrbitModelOption {
  id: OrbitPropagator;
  label: string;
  description: string;
}

export const DEFAULT_ORBIT_PROPAGATOR: OrbitPropagator = "j2-mean-elements";

export const ORBIT_MODEL_OPTIONS: OrbitModelOption[] = [
  {
    id: "j2-mean-elements",
    label: "J2 Mean Elements",
    description: "Default for parametric sessions. Includes Earth oblateness drift without requiring TLE data.",
  },
  {
    id: "keplerian-circular",
    label: "Keplerian Circular",
    description: "Simple circular motion. Useful for fast synthetic comparisons and teaching runs.",
  },
  {
    id: "sgp4-tle",
    label: "SGP4 / TLE",
    description: "Real TLE propagation. Available only when the selected constellation is TLE-backed.",
  },
];

export function constellationMode(preset: ConstellationPreset | null): string | null {
  if (!preset) return null;
  if (preset.mode) return preset.mode;
  if (!preset.constellation.trim().startsWith("{")) return null;
  try {
    const parsed = JSON.parse(preset.constellation) as { mode?: unknown };
    return typeof parsed.mode === "string" ? parsed.mode : null;
  } catch {
    return null;
  }
}

export function constellationSupportsSgp4Tle(preset: ConstellationPreset | null): boolean {
  return constellationMode(preset) === "tle";
}

export function supportedOrbitModelsForConstellation(
  preset: ConstellationPreset | null,
): OrbitModelOption[] {
  if (constellationSupportsSgp4Tle(preset)) {
    return ORBIT_MODEL_OPTIONS.filter((option) => option.id === "sgp4-tle");
  }
  return ORBIT_MODEL_OPTIONS.filter((option) => option.id !== "sgp4-tle");
}

export function defaultOrbitPropagatorForConstellation(
  preset: ConstellationPreset | null,
): OrbitPropagator {
  return constellationSupportsSgp4Tle(preset) ? "sgp4-tle" : DEFAULT_ORBIT_PROPAGATOR;
}
