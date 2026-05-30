// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Scene-unit conversion: the R3F scene shares the legacy 100-units-per-Earth-radius
 *  scale, so reused propagation/position math needs no rescale. */

import { describe, it, expect } from "vitest";
import { SCENE_EARTH_RADIUS, SCENE_KM_PER_UNIT } from "../../../sim/orbitalMath";
import { EARTH_RADIUS_KM, EARTH_RADIUS_RENDER, RENDER_UNITS_PER_KM, kmToRender } from "../units";

describe("scene units", () => {
  it("one Earth radius is EARTH_RADIUS_RENDER (= the legacy SCENE_EARTH_RADIUS) render units", () => {
    expect(EARTH_RADIUS_RENDER).toBe(SCENE_EARTH_RADIUS);
    expect(kmToRender(EARTH_RADIUS_KM)).toBeCloseTo(EARTH_RADIUS_RENDER);
  });

  it("shares the legacy scene scale exactly (no rescale factor)", () => {
    expect(RENDER_UNITS_PER_KM).toBeCloseTo(1 / SCENE_KM_PER_UNIT);
    // A 550 km LEO shell: same render radius the legacy propagator emits.
    expect(kmToRender(EARTH_RADIUS_KM + 550)).toBeCloseTo(
      SCENE_EARTH_RADIUS + 550 / SCENE_KM_PER_UNIT,
    );
  });

  it("scales linearly from zero", () => {
    expect(kmToRender(0)).toBe(0);
    expect(kmToRender(2 * EARTH_RADIUS_KM)).toBeCloseTo(2 * EARTH_RADIUS_RENDER);
  });
});
