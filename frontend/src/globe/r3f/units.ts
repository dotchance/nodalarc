// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Render-space units for the R3F scene. The scene shares ONE scale with the legacy
 * globe and its truth layer: 1 Earth radius = SCENE_EARTH_RADIUS (100) render units,
 * sourced from sim/orbitalMath so there is a single scale constant. This lets the R3F
 * components reuse the legacy propagation/position math (propagateToSceneXYZ, the SGP4
 * worker, geoToWorld, positionLookup) VERBATIM with no rescale factor — the lowest-risk
 * path to faithful parity (see specs/plans/ux2-r3f-migration.md, "Scale decision").
 *
 * Earth + LEO sits comfortably within float32 at this scale; the float64 /
 * floating-origin precision layer is deferred until a second body lands (it is keyed on
 * the per-body frame, not the absolute unit, so adding it later is an addition).
 */

import { EARTH_RADIUS_KM, SCENE_EARTH_RADIUS, SCENE_KM_PER_UNIT } from "../../sim/orbitalMath";

export { EARTH_RADIUS_KM };

/** Render units for one Earth radius (= the legacy SCENE_EARTH_RADIUS). */
export const EARTH_RADIUS_RENDER = SCENE_EARTH_RADIUS;

/** Render units per kilometer — the legacy scene scale, single-sourced. */
export const RENDER_UNITS_PER_KM = 1 / SCENE_KM_PER_UNIT;

/** Convert a distance in km (within a body's local frame) to render units. */
export function kmToRender(km: number): number {
  return km / SCENE_KM_PER_UNIT;
}
