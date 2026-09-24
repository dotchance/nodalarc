// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Deterministic grid layout for topology view.
 *  Transposed: planes as columns (X), slots as rows (Y).
 *  Bands group satellites by their legend entry in the colored instance
 *  (an area, or area border routers). GS row below.
 */

import type { NodeState, LinkState } from "../types";
import { isGroundLinkState, nodeDisplayLabel } from "../networkIdentity";

export interface LayoutNode {
  id: string;
  label: string;
  x: number;
  y: number;
  type: string;
  /** The band the node sits in; null outside the colored instance. */
  band: string | null;
  plane: number | null;
  slot: number | null;
}

export interface LayoutLink {
  nodeA: string;
  nodeB: string;
  state: string;
  isGround: boolean;
}

export interface AreaBounds {
  id: string;
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface TopologyLayout {
  nodes: LayoutNode[];
  links: LayoutLink[];
  areas: AreaBounds[];
  width: number;
  height: number;
}

const NODE_SPACING_X = 64;
const NODE_SPACING_Y = 56;
const GS_SPACING = 80;
const PLANE_GAP = 8;
const BAND_GAP = 32;
const MARGIN = 40;

export function computeLayout(
  nodes: NodeState[],
  links: LinkState[],
  bandOf: (node: NodeState) => string | null,
): TopologyLayout {
  const sats = nodes.filter((n) => n.node_type === "satellite");
  const gss = nodes.filter((n) => n.node_type === "ground_station");

  const allAreasNull = sats.every((s) => bandOf(s) == null);

  const layoutNodes: LayoutNode[] = [];
  const areaBoundsMap = new Map<string, { minX: number; minY: number; maxX: number; maxY: number }>();

  // Find max slot count for Y extent
  let maxSlotCount = 0;
  const planeSlotCounts = new Map<number, number>();
  for (const sat of sats) {
    const plane = sat.plane ?? 0;
    planeSlotCounts.set(plane, (planeSlotCounts.get(plane) ?? 0) + 1);
  }
  for (const count of planeSlotCounts.values()) {
    if (count > maxSlotCount) maxSlotCount = count;
  }

  // bandX advances as we lay out planes left to right
  let bandX = MARGIN;

  if (allAreasNull) {
    const planeMap = new Map<number, NodeState[]>();
    for (const sat of sats) {
      const plane = sat.plane ?? 0;
      if (!planeMap.has(plane)) planeMap.set(plane, []);
      planeMap.get(plane)!.push(sat);
    }
    const sortedPlanes = [...planeMap.keys()].sort((a, b) => a - b);

    for (const plane of sortedPlanes) {
      const planeSats = planeMap.get(plane)!;
      planeSats.sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0));

      for (let i = 0; i < planeSats.length; i++) {
        const sat = planeSats[i]!;
        layoutNodes.push({
          id: sat.node_id,
          label: nodeDisplayLabel(sat),
          x: bandX,
          y: MARGIN + i * NODE_SPACING_Y,
          type: "satellite",
          band: bandOf(sat),
          plane: sat.plane,
          slot: sat.slot,
        });
      }

      bandX += NODE_SPACING_X + PLANE_GAP;
    }
  } else {
    const areaMap = new Map<string, Map<number, NodeState[]>>();
    for (const sat of sats) {
      const area = bandOf(sat) ?? "";
      if (!areaMap.has(area)) areaMap.set(area, new Map());
      const planeMap = areaMap.get(area)!;
      const plane = sat.plane ?? 0;
      if (!planeMap.has(plane)) planeMap.set(plane, []);
      planeMap.get(plane)!.push(sat);
    }

    const sortedAreas = [...areaMap.keys()].sort();

    for (const area of sortedAreas) {
      const planeMap = areaMap.get(area)!;
      const sortedPlanes = [...planeMap.keys()].sort((a, b) => a - b);

      for (const plane of sortedPlanes) {
        const planeSats = planeMap.get(plane)!;
        planeSats.sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0));

        for (let i = 0; i < planeSats.length; i++) {
          const sat = planeSats[i]!;
          const x = bandX;
          const y = MARGIN + i * NODE_SPACING_Y;
          layoutNodes.push({
            id: sat.node_id,
            label: nodeDisplayLabel(sat),
            x,
            y,
            type: "satellite",
            band: bandOf(sat),
            plane: sat.plane,
            slot: sat.slot,
          });

          const bounds = areaBoundsMap.get(area);
          if (bounds) {
            bounds.minX = Math.min(bounds.minX, x);
            bounds.minY = Math.min(bounds.minY, y);
            bounds.maxX = Math.max(bounds.maxX, x);
            bounds.maxY = Math.max(bounds.maxY, y);
          } else {
            areaBoundsMap.set(area, { minX: x, minY: y, maxX: x, maxY: y });
          }
        }

        bandX += NODE_SPACING_X + PLANE_GAP;
      }

      // Extra gap between areas
      bandX += BAND_GAP - PLANE_GAP;
    }
  }

  // GS row below — center horizontally relative to satellite grid width
  const satGridWidth = bandX - MARGIN;
  const gsY = MARGIN + maxSlotCount * NODE_SPACING_Y + MARGIN;
  const gsRowWidth = gss.length > 0 ? (gss.length - 1) * GS_SPACING : 0;
  const gsStartX = MARGIN + (satGridWidth - gsRowWidth) / 2;
  for (let i = 0; i < gss.length; i++) {
    const gs = gss[i]!;
    layoutNodes.push({
      id: gs.node_id,
      label: nodeDisplayLabel(gs),
      x: gsStartX + i * GS_SPACING,
      y: gsY,
      type: "ground_station",
      band: bandOf(gs),
      plane: null,
      slot: null,
    });
  }

  // Area bounds — vertical bands now
  const AREA_PAD_Y = 16;
  const AREA_PAD_LEFT = 12;
  const AREA_PAD_RIGHT = 32;
  // Satellites outside the colored instance share the unnamed band, which has no box.
  const filteredAreaEntries = [...areaBoundsMap.entries()].filter(([id]) => id !== "");
  const areaBounds: AreaBounds[] = (areaBoundsMap.size <= 1 ? [] : filteredAreaEntries).map(([id, b]) => ({
    id,
    minX: b.minX - AREA_PAD_LEFT,
    minY: b.minY - AREA_PAD_Y,
    maxX: b.maxX + AREA_PAD_RIGHT,
    maxY: b.maxY + AREA_PAD_Y,
  }));

  const layoutLinks: LayoutLink[] = links.map((l) => ({
    nodeA: l.node_a,
    nodeB: l.node_b,
    state: l.state,
    isGround: isGroundLinkState(l),
  }));

  const maxX = bandX + MARGIN;
  const maxY = gsY + NODE_SPACING_Y + MARGIN;

  return { nodes: layoutNodes, links: layoutLinks, areas: areaBounds, width: maxX, height: maxY };
}
