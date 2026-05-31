// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Universe: the scene root — the R3F Canvas, camera rig, OrbitControls, lighting, and
 * background. Children are bodies (and, later, inter-body relay paths and the selection
 * overlay). Camera and controls reproduce the legacy globe (config.ts) at the shared
 * 100-units-per-Earth-radius scale. preserveDrawingBuffer is required for the screenshot
 * GlobeAction. The floating-origin camera rebase is added here when a second body lands.
 */

import { Suspense, useEffect, useRef, type ReactNode } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  CAMERA_FOV,
  CAMERA_DISTANCE,
  CAMERA_MIN_DISTANCE,
  CAMERA_MAX_DISTANCE,
} from "../../config";

/** OrbitControls (three addon) wired to R3F's camera + canvas, matching legacy damping. */
function Controls() {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const controlsRef = useRef<OrbitControls | null>(null);
  useEffect(() => {
    const controls = new OrbitControls(camera, gl.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.minDistance = CAMERA_MIN_DISTANCE;
    controls.maxDistance = CAMERA_MAX_DISTANCE;
    controlsRef.current = controls;
    return () => controls.dispose();
  }, [camera, gl]);
  useFrame(() => controlsRef.current?.update());
  return null;
}

export function Universe({ children }: { children?: ReactNode }) {
  return (
    <Canvas
      flat
      camera={{
        fov: CAMERA_FOV,
        position: [0, CAMERA_DISTANCE * 0.5, CAMERA_DISTANCE * 0.87],
        near: 0.1,
        far: 10000,
      }}
      gl={{ antialias: true, preserveDrawingBuffer: true }}
      dpr={[1, 2]}
    >
      <color attach="background" args={["#0d0d1a"]} />
      {/* Ambient fill only; the sun directional lives in <Earth> (earth frame), positioned by
          the sim-time sun model so the terminator tracks the frame rotation. */}
      <ambientLight intensity={0.5} />
      <Controls />
      <Suspense fallback={null}>{children}</Suspense>
    </Canvas>
  );
}
