// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import type { ConstellationPreset } from "../wizardTypes";
import {
  DEFAULT_ORBIT_PROPAGATOR,
  constellationSupportsSgp4Tle,
  defaultOrbitPropagatorForConstellation,
  supportedOrbitModelsForConstellation,
} from "../orbitModels";

function preset(mode: string | null, constellation = "configs/constellations/starlink-176.yaml"): ConstellationPreset {
  return {
    name: "test",
    description: "test",
    satellite_count: 1,
    constellation,
    ground_stations: "configs/ground-stations/sets/global.yaml",
    mode,
  };
}

describe("orbit model helpers", () => {
  it("defaults normal parametric constellations to J2 mean elements", () => {
    expect(DEFAULT_ORBIT_PROPAGATOR).toBe("j2-mean-elements");
    expect(defaultOrbitPropagatorForConstellation(preset("parametric"))).toBe("j2-mean-elements");
  });

  it("allows SGP4 only for TLE-backed constellations", () => {
    expect(constellationSupportsSgp4Tle(preset("parametric"))).toBe(false);
    expect(constellationSupportsSgp4Tle(preset("explicit"))).toBe(false);
    expect(constellationSupportsSgp4Tle(preset("tle"))).toBe(true);
  });

  it("detects inline TLE constellation sources", () => {
    const inline = preset(null, JSON.stringify({ mode: "tle", name: "tle-demo" }));

    expect(constellationSupportsSgp4Tle(inline)).toBe(true);
    expect(defaultOrbitPropagatorForConstellation(inline)).toBe("sgp4-tle");
  });

  it("lists supported orbit models by constellation source", () => {
    expect(supportedOrbitModelsForConstellation(preset("parametric")).map((option) => option.id))
      .toEqual(["j2-mean-elements", "keplerian-circular"]);
    expect(supportedOrbitModelsForConstellation(preset("tle")).map((option) => option.id))
      .toEqual(["sgp4-tle"]);
  });
});
