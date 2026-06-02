// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Shared ground-station view toggles and coverage-cone math for the R3F globe. */

import { KM_PER_UNIT } from "../config";

let gsLabelsEnabled = true;

export function setGsLabelsEnabled(enabled: boolean): void {
  gsLabelsEnabled = enabled;
}

export function getGsLabelsEnabled(): boolean {
  return gsLabelsEnabled;
}

/** Compute the surface radius, in render units, of a min-elevation coverage cone. */
export function computeConeRadius(minElevDeg: number, orbitalAltKm: number): number {
  const earthRadiusKm = 6371;
  const elevRad = (minElevDeg * Math.PI) / 180;
  const centralAngle =
    Math.acos((earthRadiusKm * Math.cos(elevRad)) / (earthRadiusKm + orbitalAltKm)) -
    elevRad;
  const arcKm = earthRadiusKm * centralAngle;
  return arcKm / KM_PER_UNIT;
}
