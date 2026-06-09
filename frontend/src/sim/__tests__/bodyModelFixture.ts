// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  bodyMathFromFrame,
  kmPerRenderUnitFromEphemeris,
  type EphemerisBodyFrame,
  type SessionEphemeris,
} from "../ephemeris";

const BODY_FILES = {
  earth: resolve(process.cwd(), "../catalog/nodalarc/bodies/earth.yaml"),
  luna: resolve(process.cwd(), "../catalog/nodalarc/bodies/luna.yaml"),
} as const;

type CatalogBodyId = keyof typeof BODY_FILES;

function bodyYaml(bodyId: CatalogBodyId): string {
  return readFileSync(BODY_FILES[bodyId], "utf-8");
}

function numericField(bodyYaml: string, key: string): number {
  const match = bodyYaml.match(new RegExp(`^\\s*${key}:\\s*([0-9.]+)\\s*$`, "m"));
  if (!match) throw new Error(`catalog body fixture missing numeric field ${key}`);
  const value = Number(match[1]);
  if (!Number.isFinite(value)) throw new Error(`catalog body fixture field ${key} is invalid`);
  return value;
}

function stringField(bodyYaml: string, key: string): string {
  const match = bodyYaml.match(new RegExp(`^\\s*${key}:\\s*(\\S+)\\s*$`, "m"));
  if (!match) throw new Error(`catalog body fixture missing string field ${key}`);
  return match[1]!;
}

export function catalogBodyRadiusKm(bodyId: CatalogBodyId): number {
  return numericField(bodyYaml(bodyId), "equatorial_radius_km");
}

export function catalogEarthFrame(): EphemerisBodyFrame {
  const earthBodyYaml = bodyYaml("earth");
  const bodyId = stringField(earthBodyYaml, "id");
  return {
    body_id: bodyId,
    mean_radius_km: numericField(earthBodyYaml, "mean_radius_km"),
    equatorial_radius_km: numericField(earthBodyYaml, "equatorial_radius_km"),
    polar_radius_km: numericField(earthBodyYaml, "polar_radius_km"),
    gravitational_parameter_km3_s2: numericField(
      earthBodyYaml,
      "gravitational_parameter_km3_s2",
    ),
    rotation_rate_rad_s: 7.2921159e-5,
    j2: 1.08262668e-3,
    origin_x_km: 0,
    origin_y_km: 0,
    origin_z_km: 0,
    vel_x_km_s: 0,
    vel_y_km_s: 0,
    vel_z_km_s: 0,
    provider: "test",
    kernel_id: "test",
    quality_tier: "test",
    frame: "gcrs",
  };
}

export function catalogEarthEphemeris(): SessionEphemeris {
  return {
    epoch_id: 0,
    sim_time: "2000-01-01T12:00:00Z",
    epoch_unix: 946728000.0,
    nodes: {},
    body_frames: { earth: catalogEarthFrame() },
  };
}

export function catalogEarthKmPerRenderUnit(): number {
  return kmPerRenderUnitFromEphemeris(catalogEarthEphemeris());
}

export function catalogEarthBodyMath() {
  const frame = catalogEarthFrame();
  return bodyMathFromFrame(frame, catalogEarthKmPerRenderUnit());
}
