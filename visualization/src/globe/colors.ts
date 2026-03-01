/** Color utilities for satellite and link rendering. */

import { AREA_COLORS, PLANE_COLORS } from "../config";
import type { ColorMode } from "../types";

/** Get hex color for a routing area string. */
export function areaColor(area: string | null): number {
  if (!area) return 0x888888;
  return AREA_COLORS[area] ?? 0x888888;
}

/** Get hex color for an orbital plane index. */
export function planeColor(plane: number | null): number {
  if (plane == null) return 0x888888;
  return PLANE_COLORS[plane % PLANE_COLORS.length] ?? 0x888888;
}

/** Get CSS color string for a routing area. */
export function areaCSSColor(area: string | null): string {
  return `#${areaColor(area).toString(16).padStart(6, "0")}`;
}

/** Get CSS color string for an orbital plane. */
export function planeCSSColor(plane: number | null): string {
  return `#${planeColor(plane).toString(16).padStart(6, "0")}`;
}

/** Get color for a node based on current mode. */
export function nodeColor(area: string | null, plane: number | null, mode: ColorMode): number {
  return mode === "area" ? areaColor(area) : planeColor(plane);
}
