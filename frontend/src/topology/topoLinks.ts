// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
/** Draw topology links on Canvas 2D.
 *  Link colors match the globe view (config.ts constants).
 */

import { FAIL_HOLD_MS, FAIL_FADE_MS } from "../config";
import type { LayoutLink, LayoutNode } from "./layout";

/** Point-to-line-segment distance for link hit testing. */
function pointToSegmentDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Hit test links — returns matching link key ("nodeA:nodeB") or null. */
export function hitTestLink(
  x: number,
  y: number,
  links: LayoutLink[],
  nodeMap: Map<string, LayoutNode>,
  threshold: number = 8,
): LayoutLink | null {
  for (const link of links) {
    const a = nodeMap.get(link.nodeA);
    const b = nodeMap.get(link.nodeB);
    if (!a || !b) continue;
    if (pointToSegmentDist(x, y, a.x, a.y, b.x, b.y) <= threshold) {
      return link;
    }
  }
  return null;
}

export function drawLinks(
  ctx: CanvasRenderingContext2D,
  links: LayoutLink[],
  nodeMap: Map<string, LayoutNode>,
  flowPath: string[] | null,
  dashOffset: number = 0,
  failTimes?: Map<string, number>,
  showIslLinks: boolean = true,
  showGroundLinks: boolean = true,
): void {
  const now = performance.now();
  const STUB_LEN = 20;

  // Precompute ring sizes for wrap detection
  const planeSlotsCount = new Map<number, number>();
  const planeSet = new Set<number>();
  for (const [, node] of nodeMap) {
    if (node.type === "satellite" && node.plane != null) {
      planeSlotsCount.set(node.plane, (planeSlotsCount.get(node.plane) ?? 0) + 1);
      planeSet.add(node.plane);
    }
  }
  const planeCount = planeSet.size;

  // Draw regular links
  for (const link of links) {
    const a = nodeMap.get(link.nodeA);
    const b = nodeMap.get(link.nodeB);
    if (!a || !b) continue;

    // Hide links based on toggle state (unless in fail-flash animation)
    if (link.state !== "failed") {
      if (link.isGround && !showGroundLinks) continue;
      if (!link.isGround && !showIslLinks) continue;
    }

    // Determine style before drawing
    let strokeStyle: string;
    let lineWidth: number;
    let dash: number[] = [];

    if (link.state === "failed") {
      const key = [link.nodeA, link.nodeB].sort().join(":");
      const ft = failTimes?.get(key) ?? now;
      const elapsed = now - ft;
      let opacity = 0.7;
      if (elapsed > FAIL_HOLD_MS) {
        opacity = 0.7 * (1 - (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS);
      }
      strokeStyle = `rgba(255, 51, 51, ${Math.max(0, opacity).toFixed(2)})`;
      lineWidth = 2;
    } else if (link.isGround) {
      strokeStyle = "rgba(0, 212, 170, 0.6)";
      lineWidth = 2;
    } else if (link.isCrossArea) {
      strokeStyle = "rgba(120, 180, 130, 0.35)";
      lineWidth = 1.5;
      dash = [4, 3];
    } else {
      strokeStyle = "rgba(120, 180, 130, 0.5)";
      lineWidth = 1.5;
    }

    // Wrap detection for ISL links
    const isSat = a.type === "satellite" && b.type === "satellite";
    let isWrap = false;
    let wrapDir: "horizontal" | "vertical" = "horizontal";

    if (isSat && a.plane != null && b.plane != null && a.slot != null && b.slot != null) {
      // Intra-plane wrap: same plane, slot difference > half ring
      // Slots are on Y axis (transposed layout), so stubs go vertical
      if (a.plane === b.plane) {
        const ringSize = planeSlotsCount.get(a.plane) ?? 0;
        if (ringSize > 0 && Math.abs(a.slot - b.slot) > ringSize / 2) {
          isWrap = true;
          wrapDir = "vertical";
        }
      }
      // Cross-plane wrap: same slot, plane difference > half plane count
      // Planes are on X axis (transposed layout), so stubs go horizontal
      if (!isWrap && a.slot === b.slot && planeCount > 0) {
        if (Math.abs(a.plane - b.plane) > planeCount / 2) {
          isWrap = true;
          wrapDir = "horizontal";
        }
      }
    }

    ctx.strokeStyle = strokeStyle;
    ctx.lineWidth = lineWidth;
    ctx.setLineDash(dash);

    if (isWrap) {
      // Draw two short stubs from each endpoint toward the wrap edge
      if (wrapDir === "horizontal") {
        // Cross-plane wrap: planes on X axis
        const aDir = a.plane! < b.plane! ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x + aDir * STUB_LEN, a.y);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x - aDir * STUB_LEN, b.y);
        ctx.stroke();
      } else {
        // Intra-plane wrap: slots on Y axis
        const aDir = a.slot! < b.slot! ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x, a.y + aDir * STUB_LEN);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x, b.y - aDir * STUB_LEN);
        ctx.stroke();
      }
    } else {
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    ctx.setLineDash([]);
  }

  // Draw flow path overlay with animated dash
  if (flowPath && flowPath.length >= 2) {
    ctx.strokeStyle = "#ff8800";
    ctx.lineWidth = 3;
    ctx.setLineDash([6, 3]);
    ctx.lineDashOffset = -dashOffset;

    for (let i = 0; i < flowPath.length - 1; i++) {
      const a = nodeMap.get(flowPath[i]!);
      const b = nodeMap.get(flowPath[i + 1]!);
      if (!a || !b) continue;

      // Ring-wrap detection: same plane, slot difference > half ring
      const isRingWrap = a.type === "satellite" && b.type === "satellite"
        && a.plane != null && b.plane != null && a.plane === b.plane
        && a.slot != null && b.slot != null
        && (() => {
          const ringSize = planeSlotsCount.get(a.plane!) ?? 0;
          return ringSize > 0 && Math.abs(a.slot! - b.slot!) > ringSize / 2;
        })();

      if (isRingWrap) {
        // Draw two short vertical stubs (slots on Y axis in transposed layout)
        const aDir = a.slot! < b.slot! ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x, a.y + aDir * STUB_LEN);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x, b.y - aDir * STUB_LEN);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;
  }
}
