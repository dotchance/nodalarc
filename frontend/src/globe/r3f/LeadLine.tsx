// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * LeadLine — the GS→best-candidate lead-line for the on-select bloom (spec "Globe On
 * Ground-Station Select": "Best candidate lead-line"). A thin straight line from the selected
 * ground station to the OME's best candidate satellite, distinct from the bowed active beams the
 * Links layer draws. Lives inside <Body id="earth"> so it reads body-LOCAL registry positions and
 * tracks the moving satellite each frame. Hidden when there is no selected GS / best candidate, or
 * before either endpoint has a position. Tinted with the eligible-unselected tone (it points at a
 * candidate, not a confirmed connection).
 */

import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame } from "@react-three/fiber";
import { FAMILY_TONE } from "../../explain/families";
import { getNodeLocalPosition } from "./positions";

const _gs = new THREE.Vector3();
const _sat = new THREE.Vector3();

export function LeadLine({ gsId, satId }: { gsId: string | null; satId: string | null }) {
  // <lineSegments> (THREE.LineSegments) not <line> — the latter collides with the SVG line
  // element in JSX typing. Two vertices = exactly one segment.
  const ref = useRef<THREE.LineSegments>(null);
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
    return g;
  }, []);

  useFrame(() => {
    const line = ref.current;
    if (!line) return;
    const ok =
      !!gsId &&
      !!satId &&
      getNodeLocalPosition(gsId, _gs) &&
      getNodeLocalPosition(satId, _sat);
    line.visible = ok;
    if (!ok) return;
    const pos = geometry.getAttribute("position") as THREE.BufferAttribute;
    pos.setXYZ(0, _gs.x, _gs.y, _gs.z);
    pos.setXYZ(1, _sat.x, _sat.y, _sat.z);
    pos.needsUpdate = true;
  });

  return (
    <lineSegments ref={ref} geometry={geometry}>
      <lineBasicMaterial
        color={FAMILY_TONE.eligible_unselected.hex}
        transparent
        opacity={0.85}
        depthWrite={false}
      />
    </lineSegments>
  );
}
