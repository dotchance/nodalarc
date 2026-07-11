// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import type { ConstellationPreset, OrbitModel } from "../wizardTypes";
import {
  constellationUnsupportedReason,
  defaultOrbitPropagatorForConstellation,
  supportedOrbitModelsForConstellation,
} from "../orbitModels";

const ORBIT_MODELS: OrbitModel[] = [
  { id: "j2_mean_elements", label: "J2 Mean Elements", description: "J2" },
  { id: "two_body", label: "Keplerian Two-Body", description: "two body" },
  { id: "sgp4_tle", label: "SGP4 / TLE", description: "TLE" },
];

function preset(
  supported: ConstellationPreset["capability"]["runtime_supported_propagators"],
  defaultPropagator: ConstellationPreset["capability"]["default_propagator"],
  unavailableReason: string | null = null,
): ConstellationPreset {
  return {
    name: "test",
    description: "test",
    satellite_count: 1,
    constellation: "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml",
    ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    default_node: "starlink-v2-mesh",
    capability: {
      source_kind: "constellation",
      runtime_supported_propagators: supported,
      default_propagator: defaultPropagator,
      unavailable_reason: unavailableReason,
    },
  };
}

describe("orbit model helpers", () => {
  it("uses the backend default instead of a browser-wide default", () => {
    expect(defaultOrbitPropagatorForConstellation(
      preset(["j2_mean_elements", "two_body"], "two_body"),
    )).toBe("two_body");
  });

  it("uses the backend unavailability reason without inferring source mode", () => {
    const reason = "crtbp is not supported by the current runtime";
    const unavailable = preset([], null, reason);

    expect(constellationUnsupportedReason(unavailable)).toBe(reason);
    expect(supportedOrbitModelsForConstellation(unavailable, ORBIT_MODELS)).toEqual([]);
  });

  it("lists exactly the source-specific models reported by the backend", () => {
    expect(supportedOrbitModelsForConstellation(
      preset(["j2_mean_elements", "two_body"], "j2_mean_elements"),
      ORBIT_MODELS,
    ).map((option) => option.id))
      .toEqual(["j2_mean_elements", "two_body"]);
    expect(supportedOrbitModelsForConstellation(
      preset(["sgp4_tle"], "sgp4_tle"),
      ORBIT_MODELS,
    ).map((option) => option.id))
      .toEqual(["sgp4_tle"]);
  });
});
