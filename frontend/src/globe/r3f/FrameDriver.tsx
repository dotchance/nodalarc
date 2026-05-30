// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * FrameDriver — applies the reference-frame rotation each frame, reproducing the legacy
 * GlobeView render-loop rotation law. The relative rotation between the Earth frame and
 * the star frame is always +gmst(simTime); the active mode decides which group carries it:
 *   earth-inertial: earthFrame.rotation.y = +gmst, starFrame = 0 (Earth visibly rotates)
 *   earth-fixed:    earthFrame.rotation.y = 0,     starFrame = -gmst (sky counter-rotates)
 *
 * gmst comes from the reused astronomy.gmstRadians on the EMA-interpolated sim time, so the
 * Earth rotation and satellite propagation stay in lockstep off one clock. Runs at a
 * negative useFrame priority so the rotation is set BEFORE any world-position consumer
 * (selection ring, links, labels, camera) reads getNodeWorldPosition this frame — and
 * negative (not positive) so R3F keeps auto-rendering rather than handing us the render loop.
 */

import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { interpolatedSimTimeMs } from "../../sim/simClock";
import { gmstRadians } from "../astronomy";
import type { ReferenceFrame } from "../../types";

/** The rotation law: which group carries +gmst, given the reference frame. Pure. */
export function frameRotations(
  gmstRad: number,
  mode: ReferenceFrame,
): { earthRotY: number; starRotY: number } {
  return mode === "earth-inertial"
    ? { earthRotY: gmstRad, starRotY: 0 }
    : { earthRotY: 0, starRotY: -gmstRad };
}

interface FrameDriverProps {
  earthFrame: React.RefObject<THREE.Group | null>;
  starFrame: React.RefObject<THREE.Group | null>;
  referenceFrame: ReferenceFrame;
}

export function FrameDriver({ earthFrame, starFrame, referenceFrame }: FrameDriverProps) {
  useFrame(() => {
    const interpMs = interpolatedSimTimeMs(performance.now());
    const gmstRad = interpMs !== null ? gmstRadians(interpMs / 1000) : 0;
    const { earthRotY, starRotY } = frameRotations(gmstRad, referenceFrame);
    if (earthFrame.current) earthFrame.current.rotation.y = earthRotY;
    if (starFrame.current) starFrame.current.rotation.y = starRotY;
  }, -2);
  return null;
}
