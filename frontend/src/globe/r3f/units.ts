// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Render-space units for the R3F scene. The scene shares one authoritative scale
 * with the simulation truth layer: 1 Earth radius = SCENE_EARTH_RADIUS (100)
 * render units, sourced from sim/orbitalMath so there is a single scale constant.
 * This lets render components reuse the shared propagation and position helpers
 * without hidden rescale factors.
 *
 * Node coordinates stay local to each body and the body frame is placed in the shared
 * universe. Cislunar distances are still small enough at this scale for the demonstrator;
 * the future Mars/Lagrange fidelity layer should add camera-relative/floating-origin render
 * coordinates without changing these truth units.
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
