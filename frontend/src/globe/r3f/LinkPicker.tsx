// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * LinkPicker — click-to-select a link beam, and click-empty-space to deselect. Reproduces the
 * link half of the legacy gpuPicker.ts: satellites and ground stations are picked by their own
 * R3F object handlers (Constellation / GroundStation), but link beams are batched fat-lines that
 * are not raycastable, so — exactly as the legacy did — a background click is hit-tested against
 * every active link in SCREEN SPACE (project both endpoints with the camera, point-to-segment
 * distance vs LINK_HIT_THRESHOLD) and the nearest within threshold is selected as {type:"link"}.
 *
 * It lives INSIDE the Canvas (it needs the live camera + canvas via useThree) but renders nothing;
 * instead it publishes its hit-test-and-select function into `handlerRef`, which Scene wires to
 * the Canvas-level onPointerMissed. R3F's Canvas onPointerMissed fires only when a click hit no
 * interactive object (empty space, the Earth, or a non-pickable beam) — precisely the legacy
 * gpuPicker "no nodeHit" branch. A hit selects the link; a miss clears the selection (the
 * deselect-on-empty-click the legacy did via onSelect(null)). The selection id is
 * linkKey() == sorted(a,b).join(":"), the same id the InfoPanel's LinkDetail matches on.
 *
 * Refinement over legacy: the hit-test honors the show toggles (an ISL/ground beam that is hidden
 * is not pickable) so a click never selects an invisible link. Modified clicks (ctrl/cmd) are
 * swallowed, matching the legacy gpuPicker (a modified click is reserved for sat orbit-pinning and
 * is a no-op on a miss).
 */

import { useEffect, useRef, type MutableRefObject } from "react";
import * as THREE from "three";
import { useThree } from "@react-three/fiber";
import { linkKey, isGroundLink } from "./linkBatch";
import { getNodeWorldPosition } from "./positions";
import type { LinkState, Selection } from "../../types";

// Legacy gpuPicker.ts LINK_HIT_THRESHOLD (NDC units).
const LINK_HIT_THRESHOLD = 0.02;

const _a = new THREE.Vector3();
const _b = new THREE.Vector3();

/** Distance from point (px,py) to segment (ax,ay)-(bx,by) — legacy gpuPicker.ts pointToSegment2D. */
function pointToSegment2D(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Nearest active, visible link within threshold of the NDC point, or null (legacy hitTestLinks). */
function hitTestLinks(
  ndcX: number,
  ndcY: number,
  camera: THREE.Camera,
  links: LinkState[],
  showIsl: boolean,
  showGnd: boolean,
): string | null {
  let bestDist = LINK_HIT_THRESHOLD;
  let bestKey: string | null = null;
  for (const ls of links) {
    if (ls.state !== "active") continue;
    const ground = isGroundLink(ls.node_a, ls.node_b);
    if (ground ? !showGnd : !showIsl) continue;
    if (!getNodeWorldPosition(ls.node_a, _a)) continue;
    if (!getNodeWorldPosition(ls.node_b, _b)) continue;
    _a.project(camera);
    _b.project(camera);
    const dist = pointToSegment2D(ndcX, ndcY, _a.x, _a.y, _b.x, _b.y);
    if (dist < bestDist) {
      bestDist = dist;
      bestKey = linkKey(ls.node_a, ls.node_b);
    }
  }
  return bestKey;
}

interface LinkPickerProps {
  links: LinkState[];
  showIslLinks: boolean;
  showGroundLinks: boolean;
  onSelect: (sel: Selection | null) => void;
  /** Scene wires this to the Canvas-level onPointerMissed. */
  handlerRef: MutableRefObject<(event: MouseEvent) => void>;
}

export function LinkPicker({
  links,
  showIslLinks,
  showGroundLinks,
  onSelect,
  handlerRef,
}: LinkPickerProps) {
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  // Read inputs through refs so the published handler closure stays stable and never goes stale.
  const linksRef = useRef(links);
  linksRef.current = links;
  const showIslRef = useRef(showIslLinks);
  showIslRef.current = showIslLinks;
  const showGndRef = useRef(showGroundLinks);
  showGndRef.current = showGroundLinks;

  useEffect(() => {
    handlerRef.current = (e: MouseEvent) => {
      // Modified click is swallowed (legacy: reserved for sat orbit-pinning; no-op on a miss).
      if (e.ctrlKey || e.metaKey) return;
      const rect = gl.domElement.getBoundingClientRect();
      const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const ndcY = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      const key = hitTestLinks(
        ndcX,
        ndcY,
        camera,
        linksRef.current,
        showIslRef.current,
        showGndRef.current,
      );
      onSelect(key ? { type: "link", id: key } : null);
    };
  }, [camera, gl, onSelect, handlerRef]);

  return null;
}
