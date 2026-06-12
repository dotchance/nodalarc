// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { classifyRegime, buildRegimeIndex, REGIME_TINT, REGIMES } from "../regime";
import type { EphemerisNode, SessionEphemeris } from "../../sim/ephemeris";

const EARTH_R = 6371;

function keplerian(over: Partial<EphemerisNode> & { semi_major_axis_km: number; eccentricity: number }): EphemerisNode {
  return {
    type: "keplerian",
    propagator: "j2-mean-elements",
    inclination_deg: 53,
    raan_deg: 0,
    argument_of_perigee_deg: 0,
    mean_anomaly_deg: 0,
    plane: 0,
    slot: 0,
    reference_body: "earth",
    frame_id: "earth",
    ...over,
  } as EphemerisNode;
}

describe("regime classification (authored orbit, never instantaneous position)", () => {
  it("classifies LEO from mean altitude", () => {
    expect(classifyRegime(keplerian({ semi_major_axis_km: EARTH_R + 550, eccentricity: 0.001 }), EARTH_R)).toBe("leo");
  });

  it("classifies MEO (GPS altitude)", () => {
    expect(classifyRegime(keplerian({ semi_major_axis_km: EARTH_R + 20180, eccentricity: 0.01 }), EARTH_R)).toBe("meo");
  });

  it("classifies GEO within the band", () => {
    expect(classifyRegime(keplerian({ semi_major_axis_km: EARTH_R + 35786, eccentricity: 0.0002 }), EARTH_R)).toBe("geo");
  });

  it("a Molniya orbit is HEO — even though its perigee dips below LEO altitudes", () => {
    // a ≈ 26560 km, e ≈ 0.74: perigee altitude ~530 km. The bird passes
    // through LEO altitudes every orbit; its identity stays HEO because
    // regime is a property of the authored orbit, not the current position.
    const molniya = keplerian({ semi_major_axis_km: 26560, eccentricity: 0.74 });
    expect(classifyRegime(molniya, EARTH_R)).toBe("heo");
  });

  it("classifies lunar-frame nodes as luna regardless of elements", () => {
    expect(
      classifyRegime(keplerian({ semi_major_axis_km: 1837, eccentricity: 0.01, reference_body: "luna" }), 1737),
    ).toBe("luna");
  });

  it("never guesses: unknown body, missing radius, and super-GEO report unclassified", () => {
    expect(classifyRegime(keplerian({ semi_major_axis_km: 5000, eccentricity: 0, reference_body: "mars" }), 3390)).toBe("unknown");
    expect(classifyRegime(keplerian({ semi_major_axis_km: EARTH_R + 550, eccentricity: 0 }), undefined)).toBe("unknown");
    expect(classifyRegime(keplerian({ semi_major_axis_km: EARTH_R + 60000, eccentricity: 0 }), EARTH_R)).toBe("unknown");
  });

  it("indexes orbiting nodes only — ground nodes have no orbit class", () => {
    const ephemeris: SessionEphemeris = {
      epoch_id: 1,
      sim_time: "2026-06-12T00:00:00Z",
      epoch_unix: 0,
      nodes: {
        "leo-sat-p00s00": keplerian({ semi_major_axis_km: EARTH_R + 550, eccentricity: 0.001 }),
        "ground-gs-x": { type: "fixed", lat_deg: 0, lon_deg: 0, alt_km: 0, reference_body: "earth", frame_id: "earth" },
      },
      body_frames: {
        earth: { body_id: "earth", mean_radius_km: EARTH_R } as SessionEphemeris["body_frames"][string],
      },
    };
    const index = buildRegimeIndex(ephemeris);
    expect(index.get("leo-sat-p00s00")).toBe("leo");
    expect(index.has("ground-gs-x")).toBe(false);
  });

  it("every regime has a tint with a parseable hex and a label", () => {
    for (const regime of REGIMES) {
      const tint = REGIME_TINT[regime];
      expect(tint.css).toMatch(/^#[0-9a-fA-F]{6}$/);
      expect(tint.hex).toBe(parseInt(tint.css.slice(1), 16));
      expect(tint.label.length).toBeGreaterThan(0);
    }
  });
});
