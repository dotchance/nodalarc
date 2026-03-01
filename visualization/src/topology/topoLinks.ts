/** Draw topology links on Canvas 2D.
 *  Per VF spec Section 6A.3:
 *   - Intra-area ISL: solid, area color at 60% opacity, 1.5px
 *   - Cross-area ISL: dashed, white at 50% opacity, 1.5px
 *   - Ground: solid, teal #00d4aa, 2px
 *   - Flow path: orange #ff8800, 3px, animated dash
 */

import { AREA_COLORS } from "../config";
import type { LayoutLink, LayoutNode } from "./layout";

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
): void {
  // Draw regular links
  for (const link of links) {
    const a = nodeMap.get(link.nodeA);
    const b = nodeMap.get(link.nodeB);
    if (!a || !b) continue;

    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);

    if (link.isGround) {
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

  // Draw flow path overlay (if any)
  if (flowPath && flowPath.length >= 2) {
    ctx.strokeStyle = "#ff8800";
    ctx.lineWidth = 3;
    ctx.setLineDash([6, 3]);

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
  }
}
