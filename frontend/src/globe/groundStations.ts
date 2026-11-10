// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Shared ground-station view toggles and coverage-cone math for the R3F globe. */

let gsLabelsEnabled = true;

export function setGsLabelsEnabled(enabled: boolean): void {
  gsLabelsEnabled = enabled;
}

export function getGsLabelsEnabled(): boolean {
  return gsLabelsEnabled;
}

/** Compute the surface radius, in render units, of a min-elevation coverage cone. */
export function computeConeRadius(
  minElevDeg: number,
  orbitalAltKm: number,
  bodyRadiusKm: number,
  kmPerRenderUnit: number,
): number {
  const elevRad = (minElevDeg * Math.PI) / 180;
  const centralAngle =
    Math.acos((bodyRadiusKm * Math.cos(elevRad)) / (bodyRadiusKm + orbitalAltKm)) -
    elevRad;
  const arcKm = bodyRadiusKm * centralAngle;
  return arcKm / kmPerRenderUnit;
}
