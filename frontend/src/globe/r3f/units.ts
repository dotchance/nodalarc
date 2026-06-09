// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Render-space units for the R3F scene. This module defines visual scale only.
 * Physical body radii and gravitational facts come from SessionEphemeris body
 * frames, which are sourced from catalog primitives.
 *
 * Node coordinates stay local to each body and the body frame is placed in the shared
 * universe. Cislunar distances are still small enough at this scale for the demonstrator;
 * the future Mars/Lagrange fidelity layer should add camera-relative/floating-origin render
 * coordinates without changing these truth units.
 */

import { SCENE_EARTH_RADIUS } from "../../sim/orbitalMath";

/** Render units for one Earth radius (= the legacy SCENE_EARTH_RADIUS). */
export const EARTH_RADIUS_RENDER = SCENE_EARTH_RADIUS;

/** Convert a distance in km (within a body's local frame) to render units. */
export function kmToRender(km: number, kmPerRenderUnit: number): number {
  return km / kmPerRenderUnit;
}
