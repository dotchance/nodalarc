// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import type { ConstellationPreset } from "../wizardTypes";
import {
  DEFAULT_ORBIT_PROPAGATOR,
  constellationUnsupportedReason,
  constellationSupportsSgp4Tle,
  defaultOrbitPropagatorForConstellation,
  supportedOrbitModelsForConstellation,
} from "../orbitModels";

function preset(
  mode: string | null,
  constellation = "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml",
): ConstellationPreset {
  return {
    name: "test",
    description: "test",
    satellite_count: 1,
    constellation,
    ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    mode,
  };
}

describe("orbit model helpers", () => {
  it("defaults normal parametric constellations to J2 mean elements", () => {
    expect(DEFAULT_ORBIT_PROPAGATOR).toBe("j2_mean_elements");
    expect(defaultOrbitPropagatorForConstellation(preset("parametric"))).toBe("j2_mean_elements");
  });

  it("detects TLE-backed constellations as structurally SGP4/TLE-only", () => {
    expect(constellationSupportsSgp4Tle(preset("parametric"))).toBe(false);
    expect(constellationSupportsSgp4Tle(preset("explicit"))).toBe(false);
    expect(constellationSupportsSgp4Tle(preset("tle"))).toBe(true);
    expect(constellationUnsupportedReason(preset("tle"))).toContain("coming soon");
  });

  it("detects inline TLE constellation sources", () => {
    const inline = preset(null, JSON.stringify({ mode: "tle", name: "tle-demo" }));

    expect(constellationSupportsSgp4Tle(inline)).toBe(true);
    expect(defaultOrbitPropagatorForConstellation(inline)).toBe("j2_mean_elements");
    expect(constellationUnsupportedReason(inline)).toContain("coming soon");
  });

  it("lists runtime-supported orbit models by constellation source", () => {
    expect(supportedOrbitModelsForConstellation(preset("parametric")).map((option) => option.id))
      .toEqual(["j2_mean_elements", "two_body"]);
    expect(supportedOrbitModelsForConstellation(preset("tle")).map((option) => option.id))
      .toEqual([]);
  });
});
