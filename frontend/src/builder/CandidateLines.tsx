// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Candidate-pair lines — the builder's link-rule preview overlay.
 *
 *  Draws straight lines between pair members using the same per-frame
 *  position registry the meshes use. This overlay is permission + geometry
 *  (what the rules allow and the epoch geometry supports) — deliberately
 *  distinct from the live view's Links layer, which renders actuated link
 *  truth. Dimmed, additive-free styling keeps candidates readable as
 *  potential, never as connected.
 */

import { useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { LINK_GROUND_COLOR, LINK_ISL_COLOR } from "../config";
import { getNodeWorldPosition } from "../globe/r3f/positions";
import type { CandidatePair } from "./candidates";

const _a = new THREE.Vector3();
const _b = new THREE.Vector3();
const _color = new THREE.Color();

function kindColor(kind: string): THREE.Color {
  return _color.set(kind === "access" ? LINK_GROUND_COLOR : LINK_ISL_COLOR);
}

export function CandidateLines({ pairs }: { pairs: CandidatePair[] }) {
  const geometryRef = useRef<THREE.BufferGeometry | null>(null);
  const pairsRef = useRef(pairs);
  pairsRef.current = pairs;
  const capacityRef = useRef(0);

  useFrame(() => {
    const geometry = geometryRef.current;
    if (!geometry) return;
    const list = pairsRef.current;

    if (capacityRef.current !== list.length) {
      capacityRef.current = list.length;
      const positions = new Float32Array(list.length * 6);
      const colors = new Float32Array(list.length * 6);
      for (let i = 0; i < list.length; i++) {
        const color = kindColor(list[i]!.kind);
        for (const vertex of [0, 1]) {
          colors[i * 6 + vertex * 3] = color.r;
          colors[i * 6 + vertex * 3 + 1] = color.g;
          colors[i * 6 + vertex * 3 + 2] = color.b;
        }
      }
      geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    }

    const attribute = geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!attribute) return;
    const array = attribute.array as Float32Array;
    for (let i = 0; i < list.length; i++) {
      const pair = list[i]!;
      if (getNodeWorldPosition(pair.a, _a) && getNodeWorldPosition(pair.b, _b)) {
        array[i * 6] = _a.x;
        array[i * 6 + 1] = _a.y;
        array[i * 6 + 2] = _a.z;
        array[i * 6 + 3] = _b.x;
        array[i * 6 + 4] = _b.y;
        array[i * 6 + 5] = _b.z;
      } else {
        // Missing registry position: collapse the segment instead of drawing
        // a line to a stale point.
        array.fill(0, i * 6, i * 6 + 6);
      }
    }
    attribute.needsUpdate = true;
  });

  if (pairs.length === 0) return null;
  return (
    <lineSegments frustumCulled={false} renderOrder={1}>
      <bufferGeometry ref={geometryRef} />
      <lineBasicMaterial vertexColors transparent opacity={0.38} depthWrite={false} />
    </lineSegments>
  );
}
