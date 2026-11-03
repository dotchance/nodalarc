// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * FrameDriver — places and turns every body group, and the starfield, each frame from
 * OME's body samples at the interpolated sim time (bodyPose.ts has the law). The inertial
 * view shows ICRF axes, so Earth turns; the Earth-fixed view shows Earth's axes, so the
 * sky and the other bodies turn around it. Both views take Earth's orientation from the
 * same samples, so satellites, ground stations and bodies stay in one frame.
 *
 * Runs at a negative useFrame priority so poses are set BEFORE any world-position consumer
 * (selection ring, links, labels, camera) reads getNodeWorldPosition this frame — and
 * negative (not positive) so R3F keeps auto-rendering rather than handing us the render loop.
 *
 * When no sample window covers the render time, or the Earth-fixed view is asked of a
 * session without Earth, it reports that through `onAvailabilityChange` and moves nothing:
 * the scene shows the state instead of a placement it does not have.
 */

import * as THREE from "three";
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { skyAt } from "../../sim/bodySamples";
import { interpolatedSimTimeMs } from "../../sim/simClock";
import type { ReferenceFrame } from "../../types";
import { bodyPose, toRenderQuaternion, toRenderVector, viewRotation } from "./bodyPose";
import { getBodyFrame } from "./positions";

/** Why the scene can or cannot place bodies at the render time. */
export type SkyAvailability =
  | "available"
  | "waiting-for-clock"
  | "no-body-samples"
  | "no-earth-for-fixed-view";

interface FrameDriverProps {
  starFrame: React.RefObject<THREE.Group | null>;
  referenceFrame: ReferenceFrame;
  kmPerRenderUnit: number;
  bodyIds: readonly string[];
  onAvailabilityChange: (availability: SkyAvailability) => void;
}

export function FrameDriver({
  starFrame,
  referenceFrame,
  kmPerRenderUnit,
  bodyIds,
  onAvailabilityChange,
}: FrameDriverProps) {
  const reported = useRef<SkyAvailability | null>(null);
  const report = (availability: SkyAvailability) => {
    if (reported.current === availability) return;
    reported.current = availability;
    onAvailabilityChange(availability);
  };

  useFrame(() => {
    const interpMs = interpolatedSimTimeMs(performance.now());
    if (interpMs === null) {
      report("waiting-for-clock");
      return;
    }
    const sky = skyAt(interpMs / 1000);
    if (!sky) {
      report("no-body-samples");
      return;
    }
    const view = viewRotation(sky, referenceFrame);
    if (!view) {
      report("no-earth-for-fixed-view");
      return;
    }
    for (const bodyId of bodyIds) {
      const pose = bodyPose(sky, bodyId, referenceFrame);
      if (!pose) {
        throw new Error(`body samples carry no state for rendered body ${bodyId}`);
      }
      const group = getBodyFrame(bodyId);
      if (!group) continue;
      toRenderVector(pose.positionKm, group.position, 1 / kmPerRenderUnit);
      toRenderQuaternion(pose.rotation, group.quaternion);
    }
    if (starFrame.current) toRenderQuaternion(view, starFrame.current.quaternion);
    report("available");
  }, -2);
  return null;
}
