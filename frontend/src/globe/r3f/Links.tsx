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
 * Truth gate: a link renders solid only when it is in the Scheduler-verified kernel-actual
 * set (`kernelActualPairs`); an OME-desired-but-not-kernel-proven link is dimmed, so a beam
 * never reads connected while the decision card says in_flight/faulted.
 *
 * Default useFrame priority (after FrameDriver -2 and Constellation -1) so the endpoint
 * positions it reads from the registry are this frame's.
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { LinkBatch, linkKey } from "./linkBatch";
import { getNodeLocalPosition } from "./positions";
import type { LinkState } from "../../types";

interface LinksProps {
  links: LinkState[];
  kernelActualPairs: [string, string][];
  showIslLinks: boolean;
  showGroundLinks: boolean;
}

export function Links({ links, kernelActualPairs, showIslLinks, showGroundLinks }: LinksProps) {
  const groupRef = useRef<THREE.Group>(null);
  const batch = useMemo(() => new LinkBatch(getNodeLocalPosition), []);
  const size = useThree((s) => s.size);
  const sizeRef = useRef(size);
  sizeRef.current = size;

  // Kernel-PROVEN link keys (matching LinkBatch's sorted key) — beams in this set render
  // solid, others dimmed. Read each frame via a ref so the useFrame closure stays stable.
  const kernelActual = useMemo(
    () => new Set(kernelActualPairs.map(([a, b]) => linkKey(a, b))),
    [kernelActualPairs],
  );
  const kernelActualRef = useRef(kernelActual);
  kernelActualRef.current = kernelActual;

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

  useFrame(() => {
    batch.animate(showIslLinks, showGroundLinks, performance.now(), kernelActualRef.current);
  });

  return <group ref={groupRef} />;
}
