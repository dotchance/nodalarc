// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * SelectionOverlay — the white billboarded selection ring and the additive glow for a
 * selected satellite. Reproduces globe/selection.ts: a flat RingGeometry(1.0,1.3,32) in the
 * WORLD frame (scene root, so it reads world positions), renderOrder 999, tracking the
 * selected node every frame via the position registry, billboarded to the camera, pulsing
 * opacity 0.4..0.8. Scale is SAT_RADIUS*3 for a satellite, SAT_RADIUS*4 for a ground
 * station. The glow sprite (additive, SAT_RADIUS*5) shows only for a selected satellite.
 * Links get no ring. The GS elevation cone is owned by <GroundStation> (toggled by selection).
 *
 * Runs at default useFrame priority (after FrameDriver -2 and Constellation -1), so the
 * world positions it reads are this frame's.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { SAT_RADIUS, SELECTION_COLOR } from "../../config";
import { getNodeWorldPosition } from "./positions";
import type { Selection } from "../../types";

/** 64x64 radial-gradient glow (globe/satellites.ts getOrCreateGlowSprite). */
function makeGlowTexture(): THREE.CanvasTexture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255, 255, 255, 0.6)");
  g.addColorStop(0.3, "rgba(255, 255, 255, 0.15)");
  g.addColorStop(1, "rgba(255, 255, 255, 0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

export function SelectionOverlay({ selection }: { selection: Selection | null }) {
  const ringRef = useRef<THREE.Mesh>(null);
  const glowRef = useRef<THREE.Sprite>(null);
  const camera = useThree((s) => s.camera);
  const glowTexture = useMemo(makeGlowTexture, []);
  const pos = useMemo(() => new THREE.Vector3(), []);
  useEffect(() => () => glowTexture.dispose(), [glowTexture]);

  useFrame(() => {
    const ring = ringRef.current;
    const glow = glowRef.current;
    if (!ring || !glow) return;
    if (!selection || selection.type === "link" || !getNodeWorldPosition(selection.id, pos)) {
      ring.visible = false;
      glow.visible = false;
      return;
    }
    const scale = selection.type === "ground_station" ? SAT_RADIUS * 4 : SAT_RADIUS * 3;
    ring.position.copy(pos);
    ring.lookAt(camera.position);
    ring.scale.setScalar(scale);
    ring.visible = true;
    const t = (Math.sin(performance.now() * 0.004) + 1) * 0.5;
    (ring.material as THREE.MeshBasicMaterial).opacity = 0.4 + t * 0.4;
    if (selection.type === "satellite") {
      glow.position.copy(pos);
      glow.scale.set(SAT_RADIUS * 5, SAT_RADIUS * 5, 1);
      glow.visible = true;
    } else {
      glow.visible = false;
    }
  });

  return (
    <>
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
      <sprite ref={glowRef} visible={false}>
        <spriteMaterial
          map={glowTexture}
          transparent
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </sprite>
    </>
  );
}
