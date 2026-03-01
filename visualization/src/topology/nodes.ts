/** Draw topology nodes on Canvas 2D. */

import { AREA_COLORS, GS_COLOR } from "../config";
import type { LayoutNode } from "./layout";

const SAT_RADIUS = 8;
const GS_RADIUS = 10;

export function drawNode(
  ctx: CanvasRenderingContext2D,
  node: LayoutNode,
  selected: boolean,
  isolated: boolean,
  isABR: boolean,
): void {
  const radius = node.type === "ground_station" ? GS_RADIUS : SAT_RADIUS;
  const color = node.type === "ground_station"
    ? `#${GS_COLOR.toString(16).padStart(6, "0")}`
    : `#${(AREA_COLORS[node.area ?? ""] ?? 0x888888).toString(16).padStart(6, "0")}`;

  ctx.globalAlpha = isolated ? 0.4 : 1.0;

  // Fill
  ctx.beginPath();
  ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();

  // ABR diamond badge — indicates Area Border Router
  if (isABR) {
    const d = radius * 0.5;
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.moveTo(node.x + radius + 3, node.y - radius);
    ctx.lineTo(node.x + radius + 3 + d, node.y - radius + d);
    ctx.lineTo(node.x + radius + 3, node.y - radius + 2 * d);
    ctx.lineTo(node.x + radius + 3 - d, node.y - radius + d);
    ctx.closePath();
    ctx.fill();
  }

  // Selection ring
  if (selected) {
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius + 4, 0, Math.PI * 2);
    ctx.stroke();
  }

  // Label
  ctx.globalAlpha = 1.0;
  ctx.fillStyle = "#e0e0e0";
  ctx.font = "9px monospace";
  ctx.textAlign = "center";
  const label = node.type === "ground_station"
    ? node.id.replace("gs-", "")
    : `P${node.plane ?? "?"}S${node.slot ?? "?"}`;
  ctx.fillText(label, node.x, node.y + radius + 12);
}

export function hitTestNode(
  x: number,
  y: number,
  nodes: LayoutNode[],
): LayoutNode | null {
  for (const node of nodes) {
    const radius = node.type === "ground_station" ? GS_RADIUS : SAT_RADIUS;
    const dx = x - node.x;
    const dy = y - node.y;
    if (dx * dx + dy * dy <= (radius + 4) * (radius + 4)) {
      return node;
    }
  }
  return null;
}
