/** Deterministic grid layout for topology view.
 *  Transposed: planes as columns (X), slots as rows (Y).
 *  Routing areas as vertical bands. GS column to the right.
 */

import type { NodeState, LinkState } from "../types";

export interface LayoutNode {
  id: string;
  x: number;
  y: number;
  type: string;
  area: string | null;
  plane: number | null;
  slot: number | null;
}

export interface LayoutLink {
  nodeA: string;
  nodeB: string;
  state: string;
  isGround: boolean;
  isCrossArea: boolean;
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
): TopologyLayout {
  const sats = nodes.filter((n) => n.node_type === "satellite");
  const gss = nodes.filter((n) => n.node_type === "ground_station");

  const allAreasNull = sats.every((s) => s.routing_area == null);

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
          x: bandX,
          y: MARGIN + i * NODE_SPACING_Y,
          type: "satellite",
          area: sat.routing_area,
          plane: sat.plane,
          slot: sat.slot,
        });
      }

      bandX += NODE_SPACING_X + PLANE_GAP;
    }
  } else {
    const areaMap = new Map<string, Map<number, NodeState[]>>();
    for (const sat of sats) {
      const area = sat.routing_area ?? "unknown";
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
            x,
            y,
            type: "satellite",
            area: sat.routing_area,
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
      x: gsStartX + i * GS_SPACING,
      y: gsY,
      type: "ground_station",
      area: gs.routing_area,
      plane: null,
      slot: null,
    });
  }

  // Area bounds — vertical bands now
  const AREA_PAD_Y = 16;
  const AREA_PAD_LEFT = 12;
  const AREA_PAD_RIGHT = 32;
  const filteredAreaEntries = [...areaBoundsMap.entries()].filter(([id]) =>
    id !== "unknown" && id !== "0.0.0.0" && id !== "",
  );
  const areaBounds: AreaBounds[] = (areaBoundsMap.size <= 1 ? [] : filteredAreaEntries).map(([id, b]) => ({
    id,
    minX: b.minX - AREA_PAD_LEFT,
    minY: b.minY - AREA_PAD_Y,
    maxX: b.maxX + AREA_PAD_RIGHT,
    maxY: b.maxY + AREA_PAD_Y,
  }));

  // Build layout links
  const nodeAreaMap = new Map<string, string | null>();
  for (const n of nodes) {
    nodeAreaMap.set(n.node_id, n.routing_area);
  }

  const layoutLinks: LayoutLink[] = links.map((l) => ({
    nodeA: l.node_a,
    nodeB: l.node_b,
    state: l.state,
    isGround: l.node_a.startsWith("gs-") || l.node_b.startsWith("gs-"),
    isCrossArea: nodeAreaMap.get(l.node_a) !== nodeAreaMap.get(l.node_b),
  }));

  const maxX = bandX + MARGIN;
  const maxY = gsY + NODE_SPACING_Y + MARGIN;

  return { nodes: layoutNodes, links: layoutLinks, areas: areaBounds, width: maxX, height: maxY };
}
