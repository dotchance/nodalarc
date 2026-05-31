// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Links — the batched ISL + ground link renderer. Wraps LinkBatch (the faithful port of
 * globe/links.ts) in the R3F lifecycle: the batch's LineSegments2 is created lazily and
 * added to this group (a child of the Earth body frame, so its local-space endpoints are
 * correct), metadata reconciles on each snapshot, and endpoints are re-resolved + uploaded
 * every frame. The fat-line material resolution tracks the actual canvas size (not the
 * window), so split-pane layouts render correct line widths.
 *
 * Default useFrame priority (after FrameDriver -2 and Constellation -1) so the endpoint
 * positions it reads from the registry are this frame's.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { useFrame, useThree } from "@react-three/fiber";
import { LinkBatch } from "./linkBatch";
import { getNodeLocalPosition } from "./positions";
import type { LinkState } from "../../types";

interface LinksProps {
  links: LinkState[];
  showIslLinks: boolean;
  showGroundLinks: boolean;
}

export function Links({ links, showIslLinks, showGroundLinks }: LinksProps) {
  const groupRef = useRef<THREE.Group>(null);
  const batch = useMemo(() => new LinkBatch(getNodeLocalPosition), []);
  const size = useThree((s) => s.size);
  const sizeRef = useRef(size);
  sizeRef.current = size;

  useEffect(() => () => batch.dispose(), [batch]);

  // Data-driven metadata reconcile on each snapshot; also (re)sync the material resolution,
  // covering the case where the batch initialized after the last size change.
  useEffect(() => {
    const g = groupRef.current;
    if (!g) return;
    batch.update(links, g, performance.now());
    batch.setResolution(sizeRef.current.width, sizeRef.current.height);
  }, [batch, links]);

  useEffect(() => {
    batch.setResolution(size.width, size.height);
  }, [batch, size]);

  const debuggedRef = useRef(false);
  useFrame((state) => {
    batch.animate(showIslLinks, showGroundLinks, performance.now());
    // TEMP r3f-debug — remove after diagnosis. Reveals the fat-line draw-time state.
    const obj = batch.object3d;
    if (!debuggedRef.current && obj) {
      debuggedRef.current = true;
      const mat = obj.material as LineMaterial;
      const v = new THREE.Vector4();
      state.gl.getViewport(v);
      // eslint-disable-next-line no-console
      console.log("[r3f-debug] links:", {
        resolution: [mat.resolution.x, mat.resolution.y],
        linewidth: mat.linewidth,
        worldUnits: mat.worldUnits,
        instanceCount: (obj.geometry as THREE.InstancedBufferGeometry).instanceCount,
        viewport: [v.z, v.w],
        dpr: state.gl.getPixelRatio(),
      });
    }
  });

  return <group ref={groupRef} />;
}
