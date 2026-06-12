// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Selection ring — a billboarded pulsing ring on the selected node. The pulse
 * runs on the WALL clock deliberately: selection must stay alive while the
 * simulation is paused. Ground stations get a slightly larger ring than
 * satellites (their glyphs are bigger); links carry no ring (the GS cone and
 * beam emphasis own link selection).
 */

import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { SAT_RADIUS, SELECTION_COLOR } from "../../config";
import { getNodeWorldPosition } from "./positions";
import type { Selection } from "../../types";

export function SelectionOverlay({ selection }: { selection: Selection | null }) {
  const ringRef = useRef<THREE.Mesh>(null);
  const camera = useThree((s) => s.camera);
  const pos = useMemo(() => new THREE.Vector3(), []);

  useFrame(() => {
    const ring = ringRef.current;
    if (!ring) return;
    if (!selection || selection.type === "link" || !getNodeWorldPosition(selection.id, pos)) {
      ring.visible = false;
      return;
    }
    const scale = selection.type === "ground_station" ? SAT_RADIUS * 4 : SAT_RADIUS * 3;
    ring.position.copy(pos);
    ring.lookAt(camera.position);
    ring.scale.setScalar(scale);
    ring.visible = true;
    const t = (Math.sin(performance.now() * 0.004) + 1) * 0.5;
    (ring.material as THREE.MeshBasicMaterial).opacity = 0.4 + t * 0.4;
  });

  return (
    <mesh ref={ringRef} renderOrder={999} visible={false}>
      <ringGeometry args={[1.0, 1.3, 32]} />
      <meshBasicMaterial
        color={SELECTION_COLOR}
        transparent
        opacity={0.7}
        side={THREE.DoubleSide}
        depthWrite={false}
      />
    </mesh>
  );
}
