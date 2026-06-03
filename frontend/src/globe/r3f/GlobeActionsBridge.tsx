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
import { CAMERA_MIN_DISTANCE } from "../../config";
import { isOccludedBySphere } from "../labels";
import { getBodyWorldSphere, getNodeBodySphere, getNodeWorldPosition } from "./positions";
import { EARTH_RADIUS_RENDER } from "./units";
import type { GlobeActions } from "../actions";
import type { CameraSceneFrame } from "./cameraBounds";
import {
  cameraDirectionFromTarget,
  focusDistanceForFrame,
  frameEndpoints,
  framePoints,
  type FocusFrame,
} from "./cameraFocus";

// Per-call temporaries (single-threaded; each entry re-copies from the registry before use).
const _world = new THREE.Vector3();
const _ndc = new THREE.Vector3();
const _dirA = new THREE.Vector3();
const _centroid = new THREE.Vector3();
const _bodyCenter = new THREE.Vector3();
const _linkA = new THREE.Vector3();
const _linkB = new THREE.Vector3();
const _followDir = new THREE.Vector3();
const _sceneCenter = new THREE.Vector3();

type ActiveFocus =
  | { kind: "body"; bodyId: string; follow: true }
  | { kind: "node"; nodeId: string; follow: boolean }
  | { kind: "link"; nodeA: string; nodeB: string; follow: boolean };

function focusLabel(focus: ActiveFocus | null): string {
  if (!focus) return "Free";
  if (focus.kind === "body") return focus.bodyId;
  if (focus.kind === "node") return `${focus.nodeId}${focus.follow ? " (follow)" : ""}`;
  return `${focus.nodeA} ↔ ${focus.nodeB}${focus.follow ? " (follow)" : ""}`;
}

function sceneFrameToFocusFrame(sceneFrame: CameraSceneFrame): FocusFrame {
  _sceneCenter.set(sceneFrame.center[0], sceneFrame.center[1], sceneFrame.center[2]);
  return { center: _sceneCenter, radius: sceneFrame.radius };
}

interface GlobeActionsBridgeProps {
  actionsRef: MutableRefObject<GlobeActions | null>;
  controlsRef: MutableRefObject<OrbitControls | null>;
  sceneFitDistance: number;
  sceneFrame: CameraSceneFrame;
  onFocusChange?: (label: string) => void;
}

export function GlobeActionsBridge({
  actionsRef,
  controlsRef,
  sceneFitDistance,
  sceneFrame,
  onFocusChange,
}: GlobeActionsBridgeProps) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const size = useThree((s) => s.size);
  // Read size through a ref so the published getNodeScreenPosition closure never goes stale.
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const focusRef = useRef<ActiveFocus | null>(null);
  const sceneFrameRef = useRef(sceneFrame);
  sceneFrameRef.current = sceneFrame;

  const resolveFocusFrame = (focus: ActiveFocus): FocusFrame | null => {
    if (focus.kind === "body") {
      const sphere = getBodyWorldSphere(focus.bodyId, _world);
      return sphere ? { center: _world, radius: sphere.radius } : null;
    }
    if (focus.kind === "node") {
      return getNodeWorldPosition(focus.nodeId, _world)
        ? { center: _world, radius: EARTH_RADIUS_RENDER * 0.08 }
        : null;
    }
    if (!getNodeWorldPosition(focus.nodeA, _linkA)) return null;
    if (!getNodeWorldPosition(focus.nodeB, _linkB)) return null;
    return frameEndpoints(_linkA, _linkB, _centroid);
  };

  const applyFrame = (
    frame: FocusFrame,
    {
      floor = CAMERA_MIN_DISTANCE * 1.15,
      distance,
    }: { floor?: number; distance?: number } = {},
  ) => {
    const controls = controlsRef.current;
    const target = frame.center.clone();
    const dist = distance ?? focusDistanceForFrame(frame, floor);
    cameraDirectionFromTarget(camera.position, target, _dirA);
    controls?.target.copy(target);
    camera.position.copy(target).add(_dirA.multiplyScalar(dist));
    controls?.update();
  };

  const setFocus = (
    focus: ActiveFocus | null,
    frame: FocusFrame | null,
    options?: { floor?: number; distance?: number },
  ) => {
    if (focus && !frame) {
      focusRef.current = null;
      onFocusChange?.(`Unavailable: ${focusLabel(focus)}`);
      return;
    }
    focusRef.current = focus;
    onFocusChange?.(focusLabel(focus));
    if (frame) applyFrame(frame, options);
  };

  const setFrameOnly = (
    label: string,
    frame: FocusFrame,
    options?: { floor?: number; distance?: number },
  ) => {
    focusRef.current = null;
    onFocusChange?.(label);
    applyFrame(frame, options);
  };

  useEffect(() => {
    const actions: GlobeActions = {
      frameScene: () => {
        setFrameOnly("Scene", sceneFrameToFocusFrame(sceneFrameRef.current), {
          distance: sceneFitDistance,
        });
      },
      focusBody: (bodyId: string) => {
        const focus: ActiveFocus = { kind: "body", bodyId, follow: true };
        const frame = resolveFocusFrame(focus);
        setFocus(focus, frame, { floor: EARTH_RADIUS_RENDER * 1.5 });
      },
      focusNode: (nodeId: string, options?: { follow?: boolean }) => {
        const focus: ActiveFocus = { kind: "node", nodeId, follow: options?.follow ?? false };
        const frame = resolveFocusFrame(focus);
        setFocus(focus, frame, { floor: CAMERA_MIN_DISTANCE * 1.15 });
      },
      focusLink: (nodeA: string, nodeB: string, options?: { follow?: boolean }) => {
        const focus: ActiveFocus = {
          kind: "link",
          nodeA,
          nodeB,
          follow: options?.follow ?? false,
        };
        const frame = resolveFocusFrame(focus);
        setFocus(focus, frame, { floor: EARTH_RADIUS_RENDER * 1.6 });
      },
      flyToTopView: () => {
        focusRef.current = null;
        onFocusChange?.("Scene");
        const controls = controlsRef.current;
        const frame = sceneFrameToFocusFrame(sceneFrameRef.current);
        camera.position.set(frame.center.x, frame.center.y + sceneFitDistance, frame.center.z);
        camera.lookAt(frame.center);
        controls?.target.copy(frame.center);
        controls?.update();
      },
      setFollowTarget: (nodeId: string | null) => {
        if (nodeId === null) {
          focusRef.current = null;
          onFocusChange?.("Free");
          return;
        }
        const focus: ActiveFocus = { kind: "node", nodeId, follow: true };
        const frame = resolveFocusFrame(focus);
        setFocus(focus, frame, { floor: CAMERA_MIN_DISTANCE * 1.15 });
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
        const focus: ActiveFocus = { kind: "node", nodeId, follow: false };
        const frame = resolveFocusFrame(focus);
        setFocus(focus, frame, { floor: CAMERA_MIN_DISTANCE * 1.15 });
      },
      flyToSegment: (nodeIds: string[]) => {
        const points: THREE.Vector3[] = [];
        for (const nodeId of nodeIds) {
          if (getNodeWorldPosition(nodeId, _world)) {
            points.push(_world.clone());
          }
        }
        const frame = framePoints(points, _centroid);
        if (frame) setFrameOnly("Segment", frame, { floor: EARTH_RADIUS_RENDER * 4 });
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
  }, [actionsRef, controlsRef, camera, gl, onFocusChange, sceneFitDistance]);

  // Follow-cam: target-locked views track the selected body/node/link while preserving the
  // operator's current orbit direction and dolly distance. This is the STK-style "reference
  // point" behavior; free/orbit views keep whatever OrbitControls target the operator panned to.
  useFrame(() => {
    const focus = focusRef.current;
    if (!focus?.follow) return;
    const frame = resolveFocusFrame(focus);
    if (!frame) return;
    const controls = controlsRef.current;
    if (!controls) return;
    const dist = Math.max(camera.position.distanceTo(controls.target), CAMERA_MIN_DISTANCE * 1.15);
    _followDir.copy(camera.position).sub(controls.target);
    if (_followDir.lengthSq() < 1e-6) _followDir.set(0, 0, 1);
    _followDir.normalize();
    controls.target.lerp(frame.center, 0.12);
    camera.position.copy(controls.target).add(_followDir.multiplyScalar(dist));
  });

  return null;
}
