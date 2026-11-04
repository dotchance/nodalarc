// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * GlobeActionsBridge — the R3F implementation of the imperative GlobeActions handle the rest of
 * the app drives (Toolbar buttons, keyboard shortcuts, NodePopover, view-switch fly-to). It lives
 * INSIDE the Canvas so it can read the live camera / renderer / canvas size via useThree, and it
 * owns the follow-cam useFrame. Node positions come from the shared registry
 * (getNodeWorldPosition), so a flight / projection sees the same per-frame truth as the meshes.
 * Framing distances are multiples of the radius of the body being framed, so a view of Mars or
 * the Moon frames that body the way a view of Earth frames Earth.
 *
 * Each focus names the body it looks at, and the bridge asks for that body as the render
 * origin (bodyPose.ts says why the world is centred on one body). FrameDriver announces the
 * move of the world's centre when it applies the new origin (shiftWorld); the bridge moves the
 * camera, the controls target and any flight in progress by the same amount, so the view does
 * not jump.
 *
 * The OrbitControls instance comes from <Universe> via controlsRef (the bridge cannot construct
 * its own — there must be exactly one). The follow-cam runs at default useFrame priority, after
 * Constellation (-1) wrote positions; Universe mounts Controls last and owns the single controls.update() call, so the follow-moved camera is consumed in the same frame. Renders no three.js objects.
 */

import { useEffect, useRef, type MutableRefObject } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import type { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  CAMERA_FLY_TO_INSTANT,
  CAMERA_FLY_TO_MAX_MS,
  CAMERA_FLY_TO_MIN_MS,
  CAMERA_FLY_TO_SPEED_UNITS_PER_SECOND,
} from "../../config";
import { isOccludedBySphere } from "../labels";
import {
  getBodyWorldSphere,
  getNodeBodySphere,
  getNodeWorldPosition,
  onWorldShift,
} from "./positions";
import type { GlobeActions } from "../actions";
import type { CameraSceneFrame } from "./cameraBounds";
import {
  cameraDirectionFromTarget,
  flightEndPosition,
  focusDistanceForFrame,
  frameEndpoints,
  framePoints,
  type AvoidSphere,
  type FocusFrame,
} from "./cameraFocus";

// Per-call temporaries (single-threaded; each entry re-copies from the registry before use).
const _world = new THREE.Vector3();
const _ndc = new THREE.Vector3();
const _centroid = new THREE.Vector3();
const _bodyCenter = new THREE.Vector3();
const _linkA = new THREE.Vector3();
const _linkB = new THREE.Vector3();
const _followDir = new THREE.Vector3();
const _sceneCenter = new THREE.Vector3();
const _flightPos = new THREE.Vector3();
const _avoidCenter = new THREE.Vector3();
const _flightTarget = new THREE.Vector3();
const _nodeBodyCenter = new THREE.Vector3();

/** The body a node lives in and that body's render radius, or null until both are registered. */
function nodeBody(nodeId: string): { body: string; radius: number } | null {
  return getNodeBodySphere(nodeId, _nodeBodyCenter);
}

type ActiveFocus =
  | { kind: "body"; bodyId: string; follow: true }
  | { kind: "node"; nodeId: string; follow: boolean }
  | { kind: "link"; nodeA: string; nodeB: string; follow: boolean };

interface CameraFlight {
  focus: ActiveFocus | null;
  startedAtMs: number;
  durationMs: number;
  startPosition: THREE.Vector3;
  startTarget: THREE.Vector3;
  endPosition: THREE.Vector3;
  endTarget: THREE.Vector3;
  endDirection: THREE.Vector3;
  floor: number;
  distance?: number;
}

function focusLabel(focus: ActiveFocus | null): string {
  if (!focus) return "Free";
  if (focus.kind === "body") return focus.bodyId;
  if (focus.kind === "node") return `${focus.nodeId}${focus.follow ? " (follow)" : ""}`;
  return `${focus.nodeA} ↔ ${focus.nodeB}${focus.follow ? " (follow)" : ""}`;
}

function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function flightDurationMs(start: THREE.Vector3, end: THREE.Vector3): number {
  const distance = start.distanceTo(end);
  const bySpeed = (distance / CAMERA_FLY_TO_SPEED_UNITS_PER_SECOND) * 1000;
  return Math.min(CAMERA_FLY_TO_MAX_MS, Math.max(CAMERA_FLY_TO_MIN_MS, bySpeed));
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
  /** The controls' closest approach to their target, from the render origin body's radius. */
  minDistance: number;
  /** Ask for a body as the render origin. */
  onOriginRequest: (bodyId: string) => void;
  onFocusChange?: (label: string) => void;
}

export function GlobeActionsBridge({
  actionsRef,
  controlsRef,
  sceneFitDistance,
  sceneFrame,
  minDistance,
  onOriginRequest,
  onFocusChange,
}: GlobeActionsBridgeProps) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const size = useThree((s) => s.size);
  // Read size through a ref so the published getNodeScreenPosition closure never goes stale.
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const focusRef = useRef<ActiveFocus | null>(null);
  const flightRef = useRef<CameraFlight | null>(null);
  const sceneFrameRef = useRef(sceneFrame);
  sceneFrameRef.current = sceneFrame;
  const minDistanceRef = useRef(minDistance);
  minDistanceRef.current = minDistance;
  const onOriginRequestRef = useRef(onOriginRequest);
  onOriginRequestRef.current = onOriginRequest;

  // A move of the world's centre moves everything positioned in world coordinates by the
  // same amount: the camera, the controls target and a flight's endpoints.
  useEffect(
    () =>
      onWorldShift((shift) => {
        camera.position.sub(shift);
        controlsRef.current?.target.sub(shift);
        const flight = flightRef.current;
        if (flight) {
          flight.startPosition.sub(shift);
          flight.startTarget.sub(shift);
          flight.endPosition.sub(shift);
          flight.endTarget.sub(shift);
        }
      }),
    [camera, controlsRef],
  );

  const resolveFocusFrame = (focus: ActiveFocus): FocusFrame | null => {
    if (focus.kind === "body") {
      const sphere = getBodyWorldSphere(focus.bodyId, _world);
      return sphere ? { center: _world, radius: sphere.radius } : null;
    }
    if (focus.kind === "node") {
      const body = nodeBody(focus.nodeId);
      return body && getNodeWorldPosition(focus.nodeId, _world)
        ? { center: _world, radius: body.radius * 0.08 }
        : null;
    }
    const bodyA = nodeBody(focus.nodeA);
    const bodyB = nodeBody(focus.nodeB);
    if (!bodyA || !bodyB) return null;
    if (!getNodeWorldPosition(focus.nodeA, _linkA)) return null;
    if (!getNodeWorldPosition(focus.nodeB, _linkB)) return null;
    return frameEndpoints(_linkA, _linkB, _centroid, Math.max(bodyA.radius, bodyB.radius) * 0.08);
  };

  /** Ask for the body a node lives in as the render origin; false when it is not known yet. */
  const requestNodeOrigin = (nodeId: string): boolean => {
    const body = nodeBody(nodeId);
    if (!body) return false;
    onOriginRequestRef.current(body.body);
    return true;
  };

  const applyFrame = (
    frame: FocusFrame,
    {
      floor = minDistanceRef.current * 1.15,
      distance,
      avoid = null,
    }: { floor?: number; distance?: number; avoid?: AvoidSphere | null } = {},
    focus: ActiveFocus | null = null,
  ) => {
    const controls = controlsRef.current;
    const target = frame.center.clone();
    const dist = distance ?? focusDistanceForFrame(frame, floor);
    const endPosition = flightEndPosition(
      target,
      camera.position,
      dist,
      avoid,
      new THREE.Vector3(),
    );
    const endDirection = endPosition.clone().sub(target).normalize();
    const startTarget = controls?.target.clone() ?? target.clone();
    if (CAMERA_FLY_TO_INSTANT) {
      flightRef.current = null;
      controls?.target.copy(target);
      camera.position.copy(endPosition);
      controls?.update();
      return;
    }
    flightRef.current = {
      focus,
      startedAtMs: performance.now(),
      durationMs: flightDurationMs(camera.position, endPosition),
      startPosition: camera.position.clone(),
      startTarget,
      endPosition,
      endTarget: target,
      endDirection,
      floor,
      distance,
    };
  };

  const setFocus = (
    focus: ActiveFocus | null,
    frame: FocusFrame | null,
    options?: { floor?: number; distance?: number; avoid?: AvoidSphere | null },
  ) => {
    if (focus && !frame) {
      focusRef.current = null;
      onFocusChange?.(`Unavailable: ${focusLabel(focus)}`);
      return;
    }
    focusRef.current = focus;
    onFocusChange?.(focusLabel(focus));
    if (frame) applyFrame(frame, options, focus);
  };

  const setFrameOnly = (
    label: string,
    frame: FocusFrame,
    options?: { floor?: number; distance?: number },
  ) => {
    focusRef.current = null;
    onFocusChange?.(label);
    applyFrame(frame, options, null);
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
        if (frame) onOriginRequestRef.current(bodyId);
        setFocus(focus, frame, { floor: frame ? frame.radius * 1.5 : 0 });
      },
      focusNode: (nodeId: string, options?: { follow?: boolean }) => {
        const focus: ActiveFocus = { kind: "node", nodeId, follow: options?.follow ?? false };
        const frame = resolveFocusFrame(focus);
        // The node's body sphere: a far-side node must be approached from
        // above its surface, never through the body.
        const bodySphere = getNodeBodySphere(nodeId, _avoidCenter);
        const avoid = bodySphere
          ? { center: _avoidCenter.clone(), radius: bodySphere.radius }
          : null;
        if (frame) requestNodeOrigin(nodeId);
        setFocus(focus, frame, { floor: (bodySphere?.radius ?? 0) * 1.05 * 1.15, avoid });
      },
      focusLink: (nodeA: string, nodeB: string, options?: { follow?: boolean }) => {
        const focus: ActiveFocus = {
          kind: "link",
          nodeA,
          nodeB,
          follow: options?.follow ?? false,
        };
        const frame = resolveFocusFrame(focus);
        const bodyA = nodeBody(nodeA);
        const bodyB = nodeBody(nodeB);
        if (frame) requestNodeOrigin(nodeA);
        setFocus(focus, frame, {
          floor: Math.max(bodyA?.radius ?? 0, bodyB?.radius ?? 0) * 1.6,
        });
      },
      flyToTopView: () => {
        focusRef.current = null;
        onFocusChange?.("Scene");
        const controls = controlsRef.current;
        const frame = sceneFrameToFocusFrame(sceneFrameRef.current);
        const target = frame.center.clone();
        const endPosition = new THREE.Vector3(
          frame.center.x,
          frame.center.y + sceneFitDistance,
          frame.center.z,
        );
        if (CAMERA_FLY_TO_INSTANT) {
          flightRef.current = null;
          camera.position.copy(endPosition);
          camera.lookAt(target);
          controls?.target.copy(target);
          controls?.update();
        } else {
          flightRef.current = {
            focus: null,
            startedAtMs: performance.now(),
            durationMs: flightDurationMs(camera.position, endPosition),
            startPosition: camera.position.clone(),
            startTarget: controls?.target.clone() ?? target.clone(),
            endPosition,
            endTarget: target,
            endDirection: cameraDirectionFromTarget(camera.position, target, new THREE.Vector3()),
            floor: minDistanceRef.current * 1.15,
            distance: sceneFitDistance,
          };
        }
      },
      setFollowTarget: (nodeId: string | null) => {
        if (nodeId === null) {
          focusRef.current = null;
          onFocusChange?.("Free");
          return;
        }
        const focus: ActiveFocus = { kind: "node", nodeId, follow: true };
        const frame = resolveFocusFrame(focus);
        if (frame) requestNodeOrigin(nodeId);
        setFocus(focus, frame, { floor: (nodeBody(nodeId)?.radius ?? 0) * 1.05 * 1.15 });
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
        if (frame) requestNodeOrigin(nodeId);
        setFocus(focus, frame, { floor: (nodeBody(nodeId)?.radius ?? 0) * 1.05 * 1.15 });
      },
      flyToSegment: (nodeIds: string[]) => {
        const points: THREE.Vector3[] = [];
        for (const nodeId of nodeIds) {
          if (getNodeWorldPosition(nodeId, _world)) {
            points.push(_world.clone());
          }
        }
        const frame = framePoints(points, _centroid);
        // A segment's nodes share one body.
        const body = nodeIds.length > 0 ? nodeBody(nodeIds[0]!) : null;
        if (!frame || !body) return;
        onOriginRequestRef.current(body.body);
        setFrameOnly("Segment", frame, { floor: body.radius * 4 });
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
    const flight = flightRef.current;
    if (flight) {
      const controls = controlsRef.current;
      if (flight.focus) {
        const frame = resolveFocusFrame(flight.focus);
        if (frame) {
          const dist = flight.distance ?? focusDistanceForFrame(frame, flight.floor);
          flight.endTarget.copy(frame.center);
          flight.endPosition.copy(frame.center).addScaledVector(flight.endDirection, dist);
        }
      }
      const elapsed = performance.now() - flight.startedAtMs;
      const raw = flight.durationMs <= 0 ? 1 : Math.min(1, elapsed / flight.durationMs);
      const t = easeInOutCubic(raw);
      _flightTarget.lerpVectors(flight.startTarget, flight.endTarget, t);
      _flightPos.lerpVectors(flight.startPosition, flight.endPosition, t);
      controls?.target.copy(_flightTarget);
      camera.position.copy(_flightPos);
      camera.lookAt(_flightTarget);
      controls?.update();
      if (raw >= 1) {
        controls?.target.copy(flight.endTarget);
        camera.position.copy(flight.endPosition);
        camera.lookAt(flight.endTarget);
        controls?.update();
        flightRef.current = null;
      }
      return;
    }

    const focus = focusRef.current;
    if (!focus?.follow) return;
    const frame = resolveFocusFrame(focus);
    if (!frame) return;
    const controls = controlsRef.current;
    if (!controls) return;
    const dist = Math.max(
      camera.position.distanceTo(controls.target),
      minDistanceRef.current * 1.15,
    );
    _followDir.copy(camera.position).sub(controls.target);
    if (_followDir.lengthSq() < 1e-6) _followDir.set(0, 0, 1);
    _followDir.normalize();
    controls.target.lerp(frame.center, 0.12);
    camera.position.copy(controls.target).add(_followDir.multiplyScalar(dist));
  });

  return null;
}
