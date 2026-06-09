// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * OrbitPins — the ctrl/cmd-click "pinned" orbit rings, rendered as ONE
 * batched LineSegments2 fat-line at the SCENE ROOT (world frame), like <AllOrbits> but for the
 * small pinned-satellite set and drawn brighter/thicker (the emphasis a pin is meant to give).
 * Each pinned satellite's ring is the great circle through its world position in the
 * position/velocity plane (orbitGeometry.computeOrbitPositions + worldVelocity), seeded
 * from the live registry world position + the view-frame rotation/angular velocity, and
 * re-seeded when the pin set or the reference frame changes. The ring is static between seed points; the satellite moves along it.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useThree } from "@react-three/fiber";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { getPlaneColor } from "../../config";
import { velocityToScene } from "../geo";
import { worldVelocity } from "../astronomy";
import { computeOrbitPositions, ORBIT_SAMPLES, supportsStaticOrbitRing } from "./orbitGeometry";
import type { NodeState, ReferenceFrame } from "../../types";
import type { SessionEphemeris } from "../../sim/ephemeris";
import { getNodeLocalPosition, getNodeWorldPosition } from "./positions";

const FLOATS_PER_ORBIT = ORBIT_SAMPLES * 6;

const _worldPos = new THREE.Vector3();
const _localPos = new THREE.Vector3();
const _velEcef = new THREE.Vector3();
const _velWorld = new THREE.Vector3();
const _color = new THREE.Color();

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

interface OrbitPinsProps {
  /** The ctrl/cmd-click pinned satellite ids. */
  pinnedIds: string[];
  nodes: NodeState[];
  earthFrame: React.RefObject<THREE.Group | null>;
  referenceFrame: ReferenceFrame;
  kmPerRenderUnit: number;
  earthRotationRateRadS: number;
  ephemeris: SessionEphemeris;
}

export function OrbitPins({
  pinnedIds,
  nodes,
  earthFrame,
  referenceFrame,
  kmPerRenderUnit,
  earthRotationRateRadS,
  ephemeris,
}: OrbitPinsProps) {
  const groupRef = useRef<THREE.Group>(null);
  const size = useThree((s) => s.size);
  const batchRef = useRef<LineSegments2 | null>(null);

  const teardown = () => {
    const g = groupRef.current;
    if (batchRef.current) {
      g?.remove(batchRef.current);
      batchRef.current.geometry.dispose();
      (batchRef.current.material as THREE.Material).dispose();
      batchRef.current = null;
    }
  };

  const pinKey = useMemo(() => [...pinnedIds].sort().join(","), [pinnedIds]);
  // Read node data through a ref: a pinned ring is seeded ONCE (at pin time / frame toggle) and is
  // static thereafter — the satellite moves along it. Rebuilding on every snapshot (byId in deps)
  // recomputed the same ring needlessly. Seed triggers are pinKey + referenceFrame only.
  const byIdRef = useRef(new Map<string, NodeState>());
  byIdRef.current = useMemo(() => new Map(nodes.map((n) => [n.node_id, n])), [nodes]);

  // Re-seed the rings when the pin set or the reference frame changes (the live rotation is
  // read from the Earth body group at seed time). Static between (the legacy seed/reseed model).
  useEffect(() => {
    teardown();
    const group = groupRef.current;
    if (!group || pinnedIds.length === 0) return;
    const rotY = earthFrame.current?.rotation.y ?? 0;
    const angVel = referenceFrame === "earth-inertial" ? earthRotationRateRadS : 0;

    const pos = new Float32Array(pinnedIds.length * FLOATS_PER_ORBIT);
    const col = new Float32Array(pinnedIds.length * FLOATS_PER_ORBIT);
    let n = 0;
    for (const id of pinnedIds) {
      const ns = byIdRef.current.get(id);
      if (!ns || ns.vel_x_km_s == null || ns.vel_y_km_s == null || ns.vel_z_km_s == null) continue;
      if (ns.plane == null) continue;
      const ephNode = ephemeris.nodes[id];
      if (
        !ephNode ||
        ephNode.type !== "keplerian" ||
        !supportsStaticOrbitRing(ephNode.eccentricity)
      ) {
        continue;
      }
      if (!getNodeWorldPosition(id, _worldPos) || !getNodeLocalPosition(id, _localPos)) continue;
      _velEcef.copy(
        velocityToScene(ns.vel_x_km_s, ns.vel_y_km_s, ns.vel_z_km_s, kmPerRenderUnit),
      );
      worldVelocity(_localPos, _velEcef, rotY, angVel, _velWorld);
      const ring = computeOrbitPositions(_worldPos, _velWorld);
      _color.setHex(getPlaneColor(ns.plane));
      const base = n * FLOATS_PER_ORBIT;
      for (let i = 0; i < ORBIT_SAMPLES; i++) {
        const i0 = i * 3;
        const i1 = (i + 1) * 3;
        const off = base + i * 6;
        pos[off] = ring[i0]!;
        pos[off + 1] = ring[i0 + 1]!;
        pos[off + 2] = ring[i0 + 2]!;
        pos[off + 3] = ring[i1]!;
        pos[off + 4] = ring[i1 + 1]!;
        pos[off + 5] = ring[i1 + 2]!;
        col[off] = _color.r;
        col[off + 1] = _color.g;
        col[off + 2] = _color.b;
        col[off + 3] = _color.r;
        col[off + 4] = _color.g;
        col[off + 5] = _color.b;
      }
      n++;
    }
    if (n === 0) return;
    const used = n * FLOATS_PER_ORBIT;
    const geometry = createGeometry(
      n < pinnedIds.length ? pos.subarray(0, used) : pos,
      n < pinnedIds.length ? col.subarray(0, used) : col,
    );
    // Opaque + depth-writing, matching the legacy orbitPins.ts material (LineMaterial defaults):
    // a pinned ring is depth-tested against the Earth so its far side is occluded, not drawn over.
    const material = new LineMaterial({
      color: 0xffffff,
      vertexColors: true,
      linewidth: 6,
      worldUnits: false,
      resolution: new THREE.Vector2(size.width, size.height),
    });
    const batch = new LineSegments2(geometry, material);
    batch.frustumCulled = false;
    group.add(batch);
    batchRef.current = batch;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinKey, referenceFrame, kmPerRenderUnit, earthRotationRateRadS, ephemeris]);

  // Keep the fat-line resolution in sync with the canvas.
  useEffect(() => {
    (batchRef.current?.material as LineMaterial | undefined)?.resolution.set(size.width, size.height);
  }, [size]);

  useEffect(() => teardown, []);

  return <group ref={groupRef} name="orbit-pins" />;
}
