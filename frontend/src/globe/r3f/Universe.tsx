// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Universe: the scene root — the R3F Canvas, camera rig, OrbitControls, lighting, and
 * background. Children are bodies (and, later, inter-body relay paths and the selection
 * overlay). Camera and controls reproduce the legacy globe (config.ts) at the shared
 * 100-units-per-Earth-radius scale. preserveDrawingBuffer is required for the screenshot
 * GlobeAction. The floating-origin camera rebase is added here when a second body lands.
 */

import { Suspense, useEffect, useRef, type MutableRefObject, type ReactNode } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  CAMERA_FOV,
  CAMERA_DISTANCE,
  CAMERA_MIN_DISTANCE,
  CAMERA_MAX_DISTANCE,
} from "../../config";

/**
 * OrbitControls (three addon) wired to R3F's camera + canvas, matching legacy damping. The
 * live instance is published into `controlsRef` so the GlobeActions bridge can drive camera
 * flights (target + update). Mounted LAST among the scene's default-priority useFrame
 * subscribers so `controls.update()` runs after the follow-cam and projection consumers have
 * had their say this frame — reproducing the legacy loop order (controls.update at the end).
 */
function Controls({
  controlsRef,
  maxDistance,
}: {
  controlsRef?: MutableRefObject<OrbitControls | null>;
  maxDistance: number;
}) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const localRef = useRef<OrbitControls | null>(null);
  const maxDistanceRef = useRef(maxDistance);
  useEffect(() => {
    maxDistanceRef.current = maxDistance;
  }, [maxDistance]);
  useEffect(() => {
    const controls = new OrbitControls(camera, gl.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.minDistance = CAMERA_MIN_DISTANCE;
    controls.maxDistance = maxDistanceRef.current;
    localRef.current = controls;
    if (controlsRef) controlsRef.current = controls;
    return () => {
      controls.dispose();
      localRef.current = null;
      if (controlsRef && controlsRef.current === controls) controlsRef.current = null;
    };
  }, [camera, gl, controlsRef]);
  useEffect(() => {
    const controls = localRef.current;
    if (!controls) return;
    controls.maxDistance = maxDistance;
    controls.update();
  }, [maxDistance]);
  useFrame(() => localRef.current?.update());
  return null;
}

function CameraClip({ far }: { far: number }) {
  const camera = useThree((s) => s.camera);
  useEffect(() => {
    camera.far = far;
    camera.updateProjectionMatrix();
  }, [camera, far]);
  return null;
}

export function Universe({
  children,
  afterControls,
  controlsRef,
  onPointerMissed,
  controlsMaxDistance = CAMERA_MAX_DISTANCE,
  cameraFar = 10000,
}: {
  children?: ReactNode;
  afterControls?: ReactNode;
  controlsRef?: MutableRefObject<OrbitControls | null>;
  /** Canvas-level miss handler: fires on a click that hit no interactive object (empty space /
   *  Earth / a non-pickable beam) — the hook the LinkPicker uses for link-select + deselect. */
  onPointerMissed?: (event: MouseEvent) => void;
  controlsMaxDistance?: number;
  cameraFar?: number;
}) {
  return (
    <Canvas
      flat
      camera={{
        fov: CAMERA_FOV,
        position: [0, CAMERA_DISTANCE * 0.5, CAMERA_DISTANCE * 0.87],
        near: 0.1,
        far: cameraFar,
      }}
      gl={{ antialias: true, preserveDrawingBuffer: true }}
      dpr={[1, 2]}
      onPointerMissed={onPointerMissed}
    >
      <color attach="background" args={["#0d0d1a"]} />
      {/* Ambient fill only; the sun directional lives in <Earth> (earth frame), positioned by
          the sim-time sun model so the terminator tracks the frame rotation. */}
      <ambientLight intensity={0.5} />
      <CameraClip far={cameraFar} />
      <Suspense fallback={null}>{children}</Suspense>
      {/* Controls run after scene writers/follow-cam; projection consumers mount after this. */}
      <Controls controlsRef={controlsRef} maxDistance={controlsMaxDistance} />
      <Suspense fallback={null}>{afterControls}</Suspense>
    </Canvas>
  );
}
