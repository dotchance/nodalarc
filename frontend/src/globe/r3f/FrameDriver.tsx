// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/**
 * FrameDriver — places and turns every body group, and the starfield, each frame from
 * OME's body samples at the interpolated sim time (bodyPose.ts has the law). The inertial
 * view shows ICRF axes, so Earth turns; the Earth-fixed view shows Earth's axes, so the
 * sky and the other bodies turn around it. Both views take Earth's orientation from the
 * same samples, so satellites, ground stations and bodies stay in one frame.
 *
 * The world is centred on the render origin body (bodyPose.ts says why). When the origin
 * changes, the frame that first applies it announces the move of the world's centre
 * (shiftWorld), this frame's vector from the old origin body to the new one, taken from the
 * same samples that place the bodies, so the camera and every cached world coordinate move
 * with it and the view does not jump.
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
import {
  bodyPose,
  relativeToOrigin,
  toRenderQuaternion,
  toRenderVector,
  viewRotation,
} from "./bodyPose";
import { getBodyFrame, shiftWorld } from "./positions";

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
  /** The body the rendered world is centred on; one of bodyIds. */
  originBodyId: string;
  onAvailabilityChange: (availability: SkyAvailability) => void;
}

const _shift = new THREE.Vector3();

export function FrameDriver({
  starFrame,
  referenceFrame,
  kmPerRenderUnit,
  bodyIds,
  originBodyId,
  onAvailabilityChange,
}: FrameDriverProps) {
  const reported = useRef<SkyAvailability | null>(null);
  const appliedOrigin = useRef<string | null>(null);
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
    const previous = appliedOrigin.current;
    if (previous !== originBodyId) {
      // A previous origin the sky no longer carries (a session switch) has no move to report.
      const move =
        previous === null ? null : relativeToOrigin(sky, originBodyId, referenceFrame, previous);
      if (move) shiftWorld(toRenderVector(move, _shift, 1 / kmPerRenderUnit));
      appliedOrigin.current = originBodyId;
    }
    for (const bodyId of bodyIds) {
      const pose = bodyPose(sky, bodyId, referenceFrame);
      const centre = relativeToOrigin(sky, bodyId, referenceFrame, originBodyId);
      if (!pose || !centre) {
        throw new Error(
          `body samples carry no state for rendered body ${bodyId} or origin ${originBodyId}`,
        );
      }
      const group = getBodyFrame(bodyId);
      if (!group) continue;
      toRenderVector(centre, group.position, 1 / kmPerRenderUnit);
      toRenderQuaternion(pose.rotation, group.quaternion);
    }
    if (starFrame.current) toRenderQuaternion(view, starFrame.current.quaternion);
    report("available");
  }, -2);
  return null;
}
