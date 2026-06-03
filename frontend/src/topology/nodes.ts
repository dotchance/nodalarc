// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Draw topology nodes on Canvas 2D. */

import { AREA_COLORS, GS_COLOR, getPlaneColor } from "../config";
import type { LayoutNode, AreaBounds } from "./layout";
import type { ColorMode } from "../types";

const SAT_RADIUS = 8;
const GS_RADIUS = 10;

function satColor(node: LayoutNode, colorMode: ColorMode): string {
  if (colorMode === "plane" && node.plane != null) {
    return `#${getPlaneColor(node.plane).toString(16).padStart(6, "0")}`;
  }
  return `#${(AREA_COLORS[node.area ?? ""] ?? 0x888888).toString(16).padStart(6, "0")}`;
}

export function drawNode(
  ctx: CanvasRenderingContext2D,
  node: LayoutNode,
  selected: boolean,
  isolated: boolean,
  isABR: boolean,
  colorMode: ColorMode = "area",
): void {
  const radius = node.type === "ground_station" ? GS_RADIUS : SAT_RADIUS;
  const color = node.type === "ground_station"
    ? `#${GS_COLOR.toString(16).padStart(6, "0")}`
    : satColor(node, colorMode);

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
  ctx.fillStyle = "#888899";
  ctx.font = "9px monospace";
  ctx.textAlign = "center";
  const label = node.type === "ground_station"
    ? node.label
    : `P${String(node.plane ?? 0).padStart(2, "0")}S${String(node.slot ?? 0).padStart(2, "0")}`;
  ctx.fillText(label, node.x, node.y + radius + 12);
}

function hexToRgb(hex: number): [number, number, number] {
  return [(hex >> 16) & 0xff, (hex >> 8) & 0xff, hex & 0xff];
}

export function drawAreaBounds(
  ctx: CanvasRenderingContext2D,
  areas: AreaBounds[],
): void {
  // Skip drawing when there's only one area or no areas
  if (areas.length <= 1) return;

  const radius = 6;
  for (const area of areas) {
    // Skip null, unknown, or 0.0.0.0 area ids
    if (!area.id || area.id === "unknown" || area.id === "0.0.0.0") continue;
    const color = AREA_COLORS[area.id] ?? 0x888888;
    const [r, g, b] = hexToRgb(color);
    const w = area.maxX - area.minX;
    const h = area.maxY - area.minY;

    // Fill
    ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.06)`;
    ctx.beginPath();
    ctx.roundRect(area.minX, area.minY, w, h, radius);
    ctx.fill();

    // Stroke
    ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, 0.35)`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(area.minX, area.minY, w, h, radius);
    ctx.stroke();

    // Label above box
    ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.5)`;
    ctx.font = "9px monospace";
    ctx.textAlign = "left";
    ctx.fillText(`Area ${area.id}`, area.minX, area.minY - 4);
  }
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
