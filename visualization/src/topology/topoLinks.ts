/** Draw topology links on Canvas 2D. */

import type { LayoutLink, LayoutNode } from "./layout";

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
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
    } else {
      // Intra-area: area color at 60% opacity
      ctx.strokeStyle = "rgba(100, 180, 100, 0.6)";
      ctx.lineWidth = 1;
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
