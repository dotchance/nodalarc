// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** Backend facts for the body-sample and satellite tests.
 *
 * fixtures/bodySamples.json is written by scripts/gen_body_sample_fixture.py from the
 * shipped earth-luna-dtn session: the SessionEphemeris and body-sample window the page
 * receives for the epoch, the window OME publishes next, and body, Sun and satellite
 * states the backend computes at instants between samples.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { BodySamples, SessionEphemeris } from "../../builder/generated/builderApi";
import type { Matrix3, Vector3 } from "../bodySamples";

export interface BodyTruth {
  position_km: Vector3;
  velocity_km_s: Vector3;
  rotation: Matrix3;
  angular_velocity_rad_s: Vector3;
}

export interface SkyTruth {
  sim_time_unix: number;
  bodies: Record<string, BodyTruth>;
  sun: { position_km: Vector3; velocity_km_s: Vector3 };
}

export interface SatelliteTruth {
  node_id: string;
  body_id: string;
  /** The Keplerian ephemeris node the backend propagated (a SessionEphemeris node, or the
   *  added Molniya J2 element set). */
  elements: Record<string, unknown>;
  states: {
    sim_time_unix: number;
    position_icrf_km: Vector3;
    position_fixed_km: Vector3;
    velocity_fixed_km_s: Vector3;
  }[];
}

export interface BodySampleFixture {
  source: string;
  ephemeris: SessionEphemeris;
  windows: [BodySamples, BodySamples];
  bodies_truth: SkyTruth[];
  satellites_truth: SatelliteTruth[];
}

const PATH = join(dirname(fileURLToPath(import.meta.url)), "fixtures/bodySamples.json");

export const BODY_SAMPLE_FIXTURE = JSON.parse(readFileSync(PATH, "utf-8")) as BodySampleFixture;
