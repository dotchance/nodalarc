// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Scene-unit conversion: the R3F scene shares the legacy 100-units-per-Earth-radius
 *  scale, so reused propagation/position math needs no rescale. */

import { describe, it, expect } from "vitest";
import { SCENE_EARTH_RADIUS } from "../../../sim/orbitalMath";
import { catalogEarthFrame, catalogEarthKmPerRenderUnit } from "../../../sim/__tests__/bodyModelFixture";
import { EARTH_RADIUS_RENDER, kmToRender } from "../units";

const EARTH_FRAME = catalogEarthFrame();
const EARTH_KM_PER_RENDER_UNIT = catalogEarthKmPerRenderUnit();

describe("scene units", () => {
  it("one Earth radius is EARTH_RADIUS_RENDER (= the legacy SCENE_EARTH_RADIUS) render units", () => {
    expect(EARTH_RADIUS_RENDER).toBe(SCENE_EARTH_RADIUS);
    expect(kmToRender(EARTH_FRAME.equatorial_radius_km, EARTH_KM_PER_RENDER_UNIT)).toBeCloseTo(
      EARTH_RADIUS_RENDER,
    );
  });

  it("shares the legacy scene scale exactly (no rescale factor)", () => {
    expect(1 / EARTH_KM_PER_RENDER_UNIT).toBeCloseTo(
      SCENE_EARTH_RADIUS / EARTH_FRAME.equatorial_radius_km,
    );
    // A 550 km LEO shell: same render radius the legacy propagator emits.
    expect(
      kmToRender(EARTH_FRAME.equatorial_radius_km + 550, EARTH_KM_PER_RENDER_UNIT),
    ).toBeCloseTo(
      SCENE_EARTH_RADIUS + 550 / EARTH_KM_PER_RENDER_UNIT,
    );
  });

  it("scales linearly from zero", () => {
    expect(kmToRender(0, EARTH_KM_PER_RENDER_UNIT)).toBe(0);
    expect(
      kmToRender(2 * EARTH_FRAME.equatorial_radius_km, EARTH_KM_PER_RENDER_UNIT),
    ).toBeCloseTo(2 * EARTH_RADIUS_RENDER);
  });
});
