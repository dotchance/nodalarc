// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Color utilities for satellite and link rendering. */

import { AREA_COLORS, PLANE_COLORS, UNKNOWN_TINT } from "../config";
import { REGIME_TINT, type Regime } from "../taxonomy/regime";
import type { ColorMode } from "../types";

/** Get hex color for a routing area string. */
export function areaColor(area: string | null): number {
  if (!area) return UNKNOWN_TINT;
  return AREA_COLORS[area] ?? UNKNOWN_TINT;
}

/** Get hex color for an orbital plane index. */
export function planeColor(plane: number | null): number {
  if (plane == null) return UNKNOWN_TINT;
  return PLANE_COLORS[plane % PLANE_COLORS.length] ?? UNKNOWN_TINT;
}

/** Get CSS color string for a routing area. */
export function areaCSSColor(area: string | null): string {
  return `#${areaColor(area).toString(16).padStart(6, "0")}`;
}

/** Get CSS color string for an orbital plane. */
export function planeCSSColor(plane: number | null): string {
  return `#${planeColor(plane).toString(16).padStart(6, "0")}`;
}

/** Get color for a node based on current mode. Regime identity comes from
 *  the authored-orbit classification (taxonomy/regime.ts), never position. */
export function nodeColor(
  area: string | null,
  plane: number | null,
  mode: ColorMode,
  regime?: Regime,
): number {
  if (mode === "regime") return REGIME_TINT[regime ?? "unknown"].hex;
  return mode === "area" ? areaColor(area) : planeColor(plane);
}
