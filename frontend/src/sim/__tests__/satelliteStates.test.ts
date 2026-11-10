// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** The page's satellite positions against the backend's.
 *
 * For Earth GEO relays, lunar relays and an eccentric J2 Earth orbit, the backend's shared
 * satellite function (lib/nodalarc/physics/satellites.py) gives body-fixed states at instants
 * between body samples, across a window replacement (fixtures/bodySamples.json). The page
 * computes the same states from the SessionEphemeris elements, each body's orbit basis, and
 * the interpolated body samples — through propagateToSceneXYZ (the worker and main-thread
 * path) and propagateNode (the Builder's path).
 */
import { describe, expect, it } from "vitest";
import type { EphemerisNodeKeplerian } from "../../builder/generated/builderApi";
import { interpolateWindows, type Vector3 } from "../bodySamples";
import { bodyMathFromFrame, ecefToGeodetic, kmPerRenderUnitFromEphemeris, propagateNode } from "../ephemeris";
import { bodyFixedToSceneXYZ, geoToSceneXYZ, propagateToSceneXYZ } from "../orbitalMath";
import { BODY_SAMPLE_FIXTURE } from "./bodySampleFixture";

const { ephemeris, windows, satellites_truth: satellites } = BODY_SAMPLE_FIXTURE;
const kmPerRenderUnit = kmPerRenderUnitFromEphemeris(ephemeris);

// Bounds: 1 cm in position and 1e-9 km/s in velocity. Measured worst cases: 0.65 mm
// (GEO relay, where the body-sample orientation error of 9.5e-12 rad is largest) and
// 5.2e-11 km/s.
const POSITION_TOL_KM = 1e-5;
const VELOCITY_TOL_KM_S = 1e-9;

function distance(a: readonly number[], b: readonly number[]): number {
  return Math.hypot(a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!);
}

function bodyOf(bodyId: string) {
  const frame = ephemeris.body_frames[bodyId as keyof typeof ephemeris.body_frames];
  if (!frame) throw new Error(`fixture ephemeris has no body frame for ${bodyId}`);
  return bodyMathFromFrame(frame, kmPerRenderUnit);
}

function expectedScene(bodyId: string, fixed: Vector3): [number, number, number] {
  if (bodyId !== "earth") return bodyFixedToSceneXYZ(fixed[0], fixed[1], fixed[2], kmPerRenderUnit);
  const body = bodyOf(bodyId);
  const geo = ecefToGeodetic(fixed[0], fixed[1], fixed[2], body);
  return geoToSceneXYZ(geo.latDeg, geo.lonDeg, geo.altKm, body.equatorialRadiusKm, kmPerRenderUnit);
}

describe("satellite states against the backend's", () => {
  it("covers Earth and lunar satellites and an eccentric J2 orbit", () => {
    const bodies = new Set(satellites.map((satellite) => satellite.body_id));
    expect(bodies).toEqual(new Set(["earth", "luna"]));
    expect(satellites.some((satellite) => satellite.node_id === "molniya-j2")).toBe(true);
  });

  for (const satellite of satellites) {
    const node = satellite.elements as unknown as EphemerisNodeKeplerian;
    const body = bodyOf(satellite.body_id);

    it(`${satellite.node_id}: scene position (worker and main-thread path)`, () => {
      let worst = 0;
      for (const state of satellite.states) {
        const sky = interpolateWindows(windows, state.sim_time_unix);
        const orientation = sky?.bodies[satellite.body_id];
        expect(orientation).toBeDefined();
        if (!orientation) continue;
        const scene = propagateToSceneXYZ(
          { ...node, body },
          ephemeris.epoch_unix,
          state.sim_time_unix,
          orientation.rotation,
        );
        const expected = expectedScene(satellite.body_id, state.position_fixed_km);
        worst = Math.max(worst, distance(scene, expected) * kmPerRenderUnit);
      }
      expect(worst).toBeLessThan(POSITION_TOL_KM);
    });

    it(`${satellite.node_id}: geodetic position and body-fixed velocity (Builder path)`, () => {
      let worstPosition = 0;
      let worstVelocity = 0;
      for (const state of satellite.states) {
        const orientation = interpolateWindows(windows, state.sim_time_unix)?.bodies[
          satellite.body_id
        ];
        expect(orientation).toBeDefined();
        if (!orientation) continue;
        const result = propagateNode(
          node,
          ephemeris.epoch_unix,
          state.sim_time_unix,
          body,
          orientation,
        );
        const fixed = state.position_fixed_km;
        const truth = ecefToGeodetic(fixed[0], fixed[1], fixed[2], body);
        const page = geoToSceneXYZ(result.latDeg, result.lonDeg, result.altKm, body.equatorialRadiusKm, 1);
        const expected = geoToSceneXYZ(truth.latDeg, truth.lonDeg, truth.altKm, body.equatorialRadiusKm, 1);
        worstPosition = Math.max(worstPosition, distance(page, expected));
        worstVelocity = Math.max(
          worstVelocity,
          distance([result.velXKmS, result.velYKmS, result.velZKmS], state.velocity_fixed_km_s),
        );
      }
      expect(worstPosition).toBeLessThan(POSITION_TOL_KM);
      expect(worstVelocity).toBeLessThan(VELOCITY_TOL_KM_S);
    });
  }
});
