/** Draw topology links on Canvas 2D.
 *  Per VF spec Section 6A.3:
 *   - Intra-area ISL: solid, area color at 60% opacity, 1.5px
 *   - Cross-area ISL: dashed, white at 50% opacity, 1.5px
 *   - Ground: solid, teal #00d4aa, 2px
 *   - Flow path: orange #ff8800, 3px, animated dash
 */

import { AREA_COLORS } from "../config";
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

function areaColorCSS(area: string | null, opacity: number): string {
  const hex = AREA_COLORS[area ?? ""] ?? 0x888888;
  const r = (hex >> 16) & 0xff;
  const g = (hex >> 8) & 0xff;
  const b = hex & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

export function drawLinks(
  ctx: CanvasRenderingContext2D,
  links: LayoutLink[],
  nodeMap: Map<string, LayoutNode>,
  flowPath: string[] | null,
  dashOffset: number = 0,
): void {
  // Draw regular links
  for (const link of links) {
    const a = nodeMap.get(link.nodeA);
    const b = nodeMap.get(link.nodeB);
    if (!a || !b) continue;

    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);

    if (link.state !== "active") {
      // Failed link: red, 2px
      ctx.strokeStyle = "rgba(255, 51, 51, 0.6)";
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
    } else if (link.isGround) {
      ctx.strokeStyle = "#00d4aa";
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
    } else if (link.isCrossArea) {
      ctx.strokeStyle = "rgba(255, 255, 255, 0.5)";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
    } else {
      // Intra-area: area color at 60% opacity
      ctx.strokeStyle = areaColorCSS(a.area, 0.6);
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

    for (let i = 0; i < flowPath.length - 1; i++) {
      const a = nodeMap.get(flowPath[i]!);
      const b = nodeMap.get(flowPath[i + 1]!);
      if (!a || !b) continue;

      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;
  }
}
