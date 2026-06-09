// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * AllOrbits — full-constellation orbit rings, the "Satellite Paths" toggle, rendered as ONE
 * batched LineSegments2 fat-line draw call. Implemented in the R3F
 * lifecycle: for each satellite with velocity + plane it derives the orbit's world-frame
 * position + velocity (registry world position + velocityToScene·worldVelocity with the live
 * view-frame rotation/angular velocity), samples a 180-segment closed ring via the reused
 * orbitGeometry.computeOrbitPositions, and packs every ring into a single position/color buffer
 * with per-vertex getPlaneColor. The rings are world-frame curves, so the batch lives at
 * SCENE ROOT (a Universe child) — NOT inside the Earth body — exactly like the world-frame renderer: the batch lives in the scene root and reads getNodeWorldPosition / worldVelocity.
 *
 * Rebuild gate is preserved verbatim: the buffer is regenerated only when the satellite COUNT
 * changes (legacy lastSatCount). On a steady count we leave the geometry alone and only sync
 * the material resolution. NaN-safe bounds: computeBoundingSphere/Box are stubbed and fixed
 * large bounds set, identical to the legacy and the link batch, so hidden/garbage vertices
 * never poison culling (the batch is frustumCulled=false anyway).
 *
 * The integrator supplies the live view-frame rotation. The frame rotation is whatever the
 * Earth body group carries this frame (set by <FrameDriver>); the angular velocity is
 * the resolved Earth body-frame rotation in earth-inertial and 0 in earth-fixed — derived here
 * from `referenceFrame` so callers pass the same two inputs FrameDriver already gets.
 *
 * useFrame runs at DEFAULT priority (0): after FrameDriver (-2) has set the frame rotation and
 * <Constellation> (-1) has written this frame's positions into the registry, so the world
 * positions and rotation it reads are current.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { getPlaneColor } from "../../config";
import { velocityToScene } from "../geo";
import { worldVelocity } from "../astronomy";
import { computeOrbitPositions, ORBIT_SAMPLES } from "./orbitGeometry";
import type { NodeState, ReferenceFrame } from "../../types";
import { getNodeLocalPosition, getNodeWorldPosition } from "./positions";

const SEGMENTS_PER_ORBIT = ORBIT_SAMPLES; // closed ring = N segments from N+1 vertices
const FLOATS_PER_ORBIT = SEGMENTS_PER_ORBIT * 6;

// Module-scope temporaries — zero per-frame heap allocation in the rebuild path.
const _worldPos = new THREE.Vector3();
const _localPos = new THREE.Vector3();
const _velEcef = new THREE.Vector3();
const _velWorld = new THREE.Vector3();
const _color = new THREE.Color();

/** NaN/garbage-safe bounds, identical to allOrbits.ts and linkBatch.ts. */
function createGeometry(positions: Float32Array, colors: Float32Array): LineSegmentsGeometry {
  const g = new LineSegmentsGeometry();
  g.computeBoundingSphere = () => {};
  g.computeBoundingBox = () => {};
  g.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), 50000);
  g.boundingBox = new THREE.Box3(
    new THREE.Vector3(-50000, -50000, -50000),
    new THREE.Vector3(50000, 50000, 50000),
  );
  g.setPositions(positions);
  g.setColors(colors);
  return g;
}

interface AllOrbitsProps {
  /** All session nodes; satellites with velocity + plane get a ring. */
  nodes: NodeState[];
  /** The "Satellite Paths" toggle. When false, the batch is torn down. */
  show: boolean;
  /**
   * Live Earth body group whose rotation.y is the current view-frame rotation (set each
   * frame by <FrameDriver>). Mirrors the legacy `earthFrame.rotation.y` argument.
   */
  earthFrame: React.RefObject<THREE.Group | null>;
  /**
   * Active reference frame — selects the frame angular velocity (dθ/dt): non-zero only in
   * earth-inertial (resolved body-frame rotation), zero in earth-fixed. Mirrors the legacy
   * `angularVelocityRadS` argument.
   */
  referenceFrame: ReferenceFrame;
  kmPerRenderUnit: number;
  earthRotationRateRadS: number;
}

export function AllOrbits({
  nodes,
  show,
  earthFrame,
  referenceFrame,
  kmPerRenderUnit,
  earthRotationRateRadS,
}: AllOrbitsProps) {
  const groupRef = useRef<THREE.Group>(null);
  const size = useThree((s) => s.size);

  const batchRef = useRef<LineSegments2 | null>(null);
  const geometryRef = useRef<LineSegmentsGeometry | null>(null);
  const materialRef = useRef<LineMaterial | null>(null);
  const lastSatCountRef = useRef(0);

  // Read props through refs so the useFrame closure stays stable and never goes stale.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const showRef = useRef(show);
  showRef.current = show;
  const refFrameRef = useRef(referenceFrame);
  refFrameRef.current = referenceFrame;
  const kmPerRenderUnitRef = useRef(kmPerRenderUnit);
  kmPerRenderUnitRef.current = kmPerRenderUnit;
  const earthRotationRateRef = useRef(earthRotationRateRadS);
  earthRotationRateRef.current = earthRotationRateRadS;
  const sizeRef = useRef(size);
  sizeRef.current = size;

  const teardown = useMemo(
    () => () => {
      const g = groupRef.current;
      if (batchRef.current) {
        g?.remove(batchRef.current);
        batchRef.current.geometry.dispose();
        (batchRef.current.material as THREE.Material).dispose();
        batchRef.current = null;
      }
      geometryRef.current = null;
      materialRef.current = null;
      lastSatCountRef.current = 0;
    },
    [],
  );

  useEffect(() => teardown, [teardown]);

  // Reference-frame toggle: the rings are world-space great circles seeded with the frame's
  // angular velocity, so they are invalid in the new frame (mirrors legacy clearAllOrbits on
  // frame toggle → lazy recreate). Tear down; the next useFrame rebuilds with the new frame's
  // parameters. The count gate alone never catches this — the sat set is unchanged on a toggle.
  useEffect(() => {
    teardown();
  }, [referenceFrame, earthRotationRateRadS, teardown]);

  // Rebuild the batch from the current satellite set.
  // Returns false if no rings were produced (count gate keeps retrying next frame).
  const rebuild = (
    sats: NodeState[],
    viewFrameRotationRad: number,
    frameAngularVelocityRadS: number,
  ): void => {
    teardown();
    const group = groupRef.current;
    if (!group) return;

    const orbitPositions = new Float32Array(sats.length * FLOATS_PER_ORBIT);
    const orbitColors = new Float32Array(sats.length * FLOATS_PER_ORBIT);
    let orbitIdx = 0;

    for (const ns of sats) {
      if (ns.vel_x_km_s == null || ns.vel_y_km_s == null || ns.vel_z_km_s == null) continue;
      if (ns.plane == null) continue;
      if (!getNodeWorldPosition(ns.node_id, _worldPos)) continue;
      if (!getNodeLocalPosition(ns.node_id, _localPos)) continue;
      _velEcef.copy(
        velocityToScene(
          ns.vel_x_km_s,
          ns.vel_y_km_s,
          ns.vel_z_km_s,
          kmPerRenderUnitRef.current,
        ),
      );
      worldVelocity(_localPos, _velEcef, viewFrameRotationRad, frameAngularVelocityRadS, _velWorld);

      const positions = computeOrbitPositions(_worldPos, _velWorld);
      _color.setHex(getPlaneColor(ns.plane));
      const r = _color.r;
      const g = _color.g;
      const b = _color.b;

      const base = orbitIdx * FLOATS_PER_ORBIT;
      for (let i = 0; i < SEGMENTS_PER_ORBIT; i++) {
        const i0 = i * 3;
        const i1 = (i + 1) * 3;
        const off = base + i * 6;
        orbitPositions[off] = positions[i0]!;
        orbitPositions[off + 1] = positions[i0 + 1]!;
        orbitPositions[off + 2] = positions[i0 + 2]!;
        orbitPositions[off + 3] = positions[i1]!;
        orbitPositions[off + 4] = positions[i1 + 1]!;
        orbitPositions[off + 5] = positions[i1 + 2]!;
        orbitColors[off] = r;
        orbitColors[off + 1] = g;
        orbitColors[off + 2] = b;
        orbitColors[off + 3] = r;
        orbitColors[off + 4] = g;
        orbitColors[off + 5] = b;
      }
      orbitIdx++;
    }

    if (orbitIdx === 0) return;

    // Trim to actual count (some sats may have been skipped). Subarray, not copy.
    const usedFloats = orbitIdx * FLOATS_PER_ORBIT;
    const posBuf = orbitIdx < sats.length ? orbitPositions.subarray(0, usedFloats) : orbitPositions;
    const colBuf = orbitIdx < sats.length ? orbitColors.subarray(0, usedFloats) : orbitColors;

    const geometry = createGeometry(posBuf, colBuf);
    const material = new LineMaterial({
      color: 0xffffff,
      vertexColors: true,
      linewidth: 2,
      worldUnits: false,
      transparent: true,
      opacity: 0.2,
      depthWrite: false,
      resolution: new THREE.Vector2(sizeRef.current.width, sizeRef.current.height),
    });
    const batch = new LineSegments2(geometry, material);
    batch.frustumCulled = false;
    group.add(batch);

    batchRef.current = batch;
    geometryRef.current = geometry;
    materialRef.current = material;
    // Gate on the input sat count (matching legacy `sats.size`), not the produced ring count,
    // so a transient skip (no position yet) doesn't force a rebuild every frame once seeded.
    lastSatCountRef.current = sats.length;
  };

  useFrame(() => {
    if (!showRef.current) {
      teardown();
      return;
    }
    const sats = nodesRef.current.filter((n) => n.node_type === "satellite");
    // Steady state: only sync the fat-line resolution (matching legacy behavior).
    if (sats.length === lastSatCountRef.current && batchRef.current) {
      materialRef.current?.resolution.set(sizeRef.current.width, sizeRef.current.height);
      return;
    }
    const viewFrameRotationRad = earthFrame.current?.rotation.y ?? 0;
    const frameAngularVelocityRadS =
      refFrameRef.current === "earth-inertial" ? earthRotationRateRef.current : 0;
    rebuild(sats, viewFrameRotationRad, frameAngularVelocityRadS);
  });

  return <group ref={groupRef} name="all-orbits" />;
}
