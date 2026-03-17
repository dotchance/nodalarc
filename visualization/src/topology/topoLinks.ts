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

    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);

    if (link.state === "failed") {
      // Fail-flash: red hold then fade
      const key = [link.nodeA, link.nodeB].sort().join(":");
      const ft = failTimes?.get(key) ?? now;
      const elapsed = now - ft;
      let opacity = 0.7;
      if (elapsed > FAIL_HOLD_MS) {
        opacity = 0.7 * (1 - (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS);
      }
      ctx.strokeStyle = `rgba(255, 51, 51, ${Math.max(0, opacity).toFixed(2)})`;
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
    } else if (link.isGround) {
      ctx.strokeStyle = "rgba(0, 212, 170, 0.6)";
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
    } else if (link.isCrossArea) {
      ctx.strokeStyle = "rgba(255, 255, 255, 0.2)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
    } else {
      ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([]);
    }

    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Draw flow path overlay with animated dash
  if (flowPath && flowPath.length >= 2) {
    ctx.strokeStyle = "#ff8800";
    ctx.lineWidth = 3;
    ctx.setLineDash([6, 3]);
    ctx.lineDashOffset = -dashOffset;

    // Compute ring size per plane for ring-wrap detection
    const planeSlotsCount = new Map<number, number>();
    for (const [, node] of nodeMap) {
      if (node.type === "satellite" && node.plane != null) {
        planeSlotsCount.set(node.plane, (planeSlotsCount.get(node.plane) ?? 0) + 1);
      }
    }

    const STUB_LEN = 20;

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
        // Draw two short horizontal stubs instead of a long diagonal
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x + (a.slot! < b.slot! ? -STUB_LEN : STUB_LEN), a.y);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x + (b.slot! < a.slot! ? -STUB_LEN : STUB_LEN), b.y);
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
