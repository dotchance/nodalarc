// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Render-space units for the R3F scene. One Earth radius == one render unit, so
 * near-Earth geometry (Earth + LEO) sits comfortably within float32 today. The
 * float64 / floating-origin precision layer is deferred until a second body lands
 * (see the UX plan, "multi-scale precision"); because the scene is already framed
 * per body, adding it later is an addition, not a rewrite.
 */

export const EARTH_RADIUS_KM = 6371;

/** Render units per kilometer. */
export const RENDER_UNITS_PER_KM = 1 / EARTH_RADIUS_KM;

/** Convert a distance in km (within a body's local frame) to render units. */
export function kmToRender(km: number): number {
  return km * RENDER_UNITS_PER_KM;
}
