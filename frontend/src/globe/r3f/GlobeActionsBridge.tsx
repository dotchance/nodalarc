// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * GlobeActionsBridge — the R3F implementation of the imperative GlobeActions handle the rest of
 * the app drives (Toolbar buttons, keyboard shortcuts, NodePopover, view-switch fly-to). It lives
 * INSIDE the Canvas so it can read the live camera / renderer / canvas size via useThree, and it
 * owns the follow-cam useFrame. Every method reproduces the legacy GlobeView actionsRef block
 * verbatim at the shared 100-units-per-Earth-radius scale (EARTH_RADIUS_RENDER == legacy
 * EARTH_RADIUS), reading node positions from the shared registry (getNodeWorldPosition) so a
 * flight / projection sees the same per-frame truth as the meshes.
 *
 * The OrbitControls instance comes from <Universe> via controlsRef (the bridge cannot construct
 * its own — there must be exactly one). The follow-cam runs at default useFrame priority, after
 * Constellation (-1) wrote positions; Universe mounts Controls LAST and owns the single controls.update() call, so the follow-moved camera is consumed in the same frame. Renders no three.js objects.
 */

import { useEffect, useRef, type MutableRefObject } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import type { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CAMERA_FOV } from "../../config";
import { isOccludedBySphere } from "../labels";
import { getNodeBodySphere, getNodeWorldPosition } from "./positions";
import { EARTH_RADIUS_RENDER } from "./units";
import type { GlobeActions } from "../actions";

// Per-call temporaries (single-threaded; each entry re-copies from the registry before use).
const _world = new THREE.Vector3();
const _ndc = new THREE.Vector3();
const _dirA = new THREE.Vector3();
const _camDir = new THREE.Vector3();
const _centroid = new THREE.Vector3();
const _fallback = new THREE.Vector3();
const _bodyCenter = new THREE.Vector3();
const HALF_FOV_SIN = Math.sin((CAMERA_FOV * Math.PI) / 360);

function fitDistanceForRadius(radius: number, floor: number): number {
  if (radius <= 0) return floor;
  return Math.max(floor, (radius / Math.max(0.001, HALF_FOV_SIN)) * 1.35);
}

interface GlobeActionsBridgeProps {
  actionsRef: MutableRefObject<GlobeActions | null>;
  controlsRef: MutableRefObject<OrbitControls | null>;
  sceneFitDistance: number;
}

export function GlobeActionsBridge({
  actionsRef,
  controlsRef,
  sceneFitDistance,
}: GlobeActionsBridgeProps) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const size = useThree((s) => s.size);
  // Read size through a ref so the published getNodeScreenPosition closure never goes stale.
  const sizeRef = useRef(size);
  sizeRef.current = size;
  // The followed node id (legacy followTargetRef): set by setFollowTarget, read by the follow-cam.
  const followTargetRef = useRef<string | null>(null);

  useEffect(() => {
    const actions: GlobeActions = {
      flyToTopView: () => {
        const controls = controlsRef.current;
        const topDist = Math.max(EARTH_RADIUS_RENDER * 4, sceneFitDistance);
        camera.position.set(0, topDist, 0);
        camera.lookAt(0, 0, 0);
        controls?.target.set(0, 0, 0);
        controls?.update();
      },
      setFollowTarget: (nodeId: string | null) => {
        followTargetRef.current = nodeId;
      },
      captureScreenshot: () => {
        // Composite the live drawing buffer (preserveDrawingBuffer is set in Universe) onto a 2D
        // canvas and stamp the UTC watermark, then trigger a PNG download — legacy verbatim.
        const canvas = gl.domElement;
        const w = canvas.width;
        const h = canvas.height;
        const ctx2d = document.createElement("canvas");
        ctx2d.width = w;
        ctx2d.height = h;
        const ctx = ctx2d.getContext("2d")!;
        ctx.drawImage(canvas, 0, 0);
        const now = new Date().toISOString().replace("T", " ").substring(0, 19) + " UTC";
        const label = `Nodal Arc — ${now}`;
        ctx.font = `${Math.max(12, h * 0.015)}px monospace`;
        ctx.fillStyle = "rgba(255, 255, 255, 0.6)";
        ctx.textAlign = "right";
        ctx.fillText(label, w - 12, h - 12);
        const link = document.createElement("a");
        link.download = `nodalarc-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
        link.href = ctx2d.toDataURL("image/png");
        link.click();
      },
      flyToNode: (nodeId: string) => {
        const controls = controlsRef.current;
        if (getNodeWorldPosition(nodeId, _world)) {
          const target = _world.clone();
          const currentTarget = controls?.target ?? _fallback.set(0, 0, 0);
          const dist = Math.max(camera.position.distanceTo(currentTarget), EARTH_RADIUS_RENDER * 2.5);
          _dirA.copy(camera.position).sub(target);
          if (_dirA.lengthSq() < 1e-6) _dirA.set(0, 0, 1);
          _dirA.normalize();
          controls?.target.copy(target);
          camera.position.copy(target).add(_dirA.multiplyScalar(dist));
          controls?.update();
        }
      },
      flyToSegment: (nodeIds: string[]) => {
        const controls = controlsRef.current;
        _centroid.set(0, 0, 0);
        _fallback.set(0, 0, 0);
        let count = 0;
        for (const nodeId of nodeIds) {
          if (getNodeWorldPosition(nodeId, _world)) {
            if (count === 0 || _world.lengthSq() > _fallback.lengthSq()) {
              _fallback.copy(_world);
            }
            _centroid.add(_world);
            count += 1;
          }
        }
        if (count === 0) return;
        _centroid.multiplyScalar(1 / count);
        if (_centroid.lengthSq() < 1e-6) _centroid.copy(_fallback);
        if (_centroid.lengthSq() === 0) return;
        let radius = 0;
        for (const nodeId of nodeIds) {
          if (getNodeWorldPosition(nodeId, _world)) {
            radius = Math.max(radius, _world.distanceTo(_centroid));
          }
        }
        const target = _centroid.clone();
        const currentTarget = controls?.target ?? _fallback.set(0, 0, 0);
        const dist = Math.max(
          camera.position.distanceTo(currentTarget),
          fitDistanceForRadius(radius, EARTH_RADIUS_RENDER * 4),
        );
        _dirA.copy(camera.position).sub(target);
        if (_dirA.lengthSq() < 1e-6) _dirA.set(0, 0, 1);
        _dirA.normalize();
        controls?.target.copy(target);
        camera.position.copy(target).add(_dirA.multiplyScalar(dist));
        controls?.update();
      },
      getNodeScreenPosition: (nodeId: string) => {
        if (!getNodeWorldPosition(nodeId, _world)) return null;
        _ndc.copy(_world).project(camera);
        // Behind camera.
        if (_ndc.z > 1) return { x: 0, y: 0, visible: false };
        // Body-limb occlusion: each node is hidden only by its own body frame. Earth-only
        // sessions match the legacy action path; lunar nodes are not tested against Earth origin.
        const cameraPos = camera.position;
        const bodySphere = getNodeBodySphere(nodeId, _bodyCenter);
        if (bodySphere && isOccludedBySphere(
          _world.x, _world.y, _world.z,
          cameraPos.x, cameraPos.y, cameraPos.z,
          _bodyCenter.x, _bodyCenter.y, _bodyCenter.z,
          bodySphere.radius,
        )) {
          return { x: 0, y: 0, visible: false };
        }
        const w = sizeRef.current.width;
        const h = sizeRef.current.height;
        const x = (_ndc.x * 0.5 + 0.5) * w;
        const y = (-_ndc.y * 0.5 + 0.5) * h;
        return { x, y, visible: true };
      },
    };
    actionsRef.current = actions;
    return () => {
      if (actionsRef.current === actions) actionsRef.current = null;
    };
  }, [actionsRef, controlsRef, camera, gl, sceneFitDistance]);

  // Follow-cam: lerp the camera 5%/frame toward the followed node, pivot fixed at the origin
  // (legacy followTarget loop). World position required — camera math is in world space.
  useFrame(() => {
    const id = followTargetRef.current;
    if (!id || !getNodeWorldPosition(id, _world)) return;
    const controls = controlsRef.current;
    const target = controls?.target ?? _fallback.set(0, 0, 0);
    const dist = Math.max(camera.position.distanceTo(target), EARTH_RADIUS_RENDER * 2.5);
    _camDir.copy(camera.position).sub(target).normalize();
    target.lerp(_world, 0.05);
    camera.position.copy(target).add(_camDir.multiplyScalar(dist));
  });

  return null;
}
