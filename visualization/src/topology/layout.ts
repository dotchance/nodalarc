/** Deterministic grid layout for topology view.
 *  Routing areas as horizontal bands with one row per plane, GS row below.
 *  VF spec Section 6A.1: satellites within each area arranged in rows by plane.
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

const NODE_SPACING_X = 80;
const NODE_SPACING_Y = 64;
const GS_SPACING = 100;
const PLANE_GAP = 8;
const BAND_GAP = 32;
const MARGIN = 40;

export function computeLayout(
  nodes: NodeState[],
  links: LinkState[],
): TopologyLayout {
  const sats = nodes.filter((n) => n.node_type === "satellite");
  const gss = nodes.filter((n) => n.node_type === "ground_station");

  // Check if all areas are null/undefined — if so, skip area grouping
  const allAreasNull = sats.every((s) => s.routing_area == null);

  const layoutNodes: LayoutNode[] = [];
  const areaBoundsMap = new Map<string, { minX: number; minY: number; maxX: number; maxY: number }>();
  let bandY = MARGIN;
  let maxSlotCount = 0;

  if (allAreasNull) {
    // No area grouping — lay out by plane directly (no band gaps, no area separation)
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
      if (planeSats.length > maxSlotCount) maxSlotCount = planeSats.length;

      for (let i = 0; i < planeSats.length; i++) {
        const sat = planeSats[i]!;
        const x = MARGIN + i * NODE_SPACING_X;
        const y = bandY;
        layoutNodes.push({
          id: sat.node_id,
          x,
          y,
          type: "satellite",
          area: sat.routing_area,
          plane: sat.plane,
          slot: sat.slot,
        });
      }

      bandY += NODE_SPACING_Y + PLANE_GAP;
    }
  } else {
    // Group satellites by area, then by plane within each area
    const areaMap = new Map<string, Map<number, NodeState[]>>();
    for (const sat of sats) {
      const area = sat.routing_area ?? "unknown";
      if (!areaMap.has(area)) areaMap.set(area, new Map());
      const planeMap = areaMap.get(area)!;
      const plane = sat.plane ?? 0;
      if (!planeMap.has(plane)) planeMap.set(plane, []);
      planeMap.get(plane)!.push(sat);
    }

    // Sort areas for deterministic order
    const sortedAreas = [...areaMap.keys()].sort();

    for (const area of sortedAreas) {
      const planeMap = areaMap.get(area)!;
      const sortedPlanes = [...planeMap.keys()].sort((a, b) => a - b);

      for (const plane of sortedPlanes) {
        const planeSats = planeMap.get(plane)!;
        // Sort by slot for deterministic layout
        planeSats.sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0));
        if (planeSats.length > maxSlotCount) maxSlotCount = planeSats.length;

        for (let i = 0; i < planeSats.length; i++) {
          const sat = planeSats[i]!;
          const x = MARGIN + i * NODE_SPACING_X;
          const y = bandY;
          layoutNodes.push({
            id: sat.node_id,
            x,
            y,
            type: "satellite",
            area: sat.routing_area,
            plane: sat.plane,
            slot: sat.slot,
          });

          // Track area bounds
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

        bandY += NODE_SPACING_Y + PLANE_GAP;
      }

      // Extra gap between areas (subtract the plane gap we just added, add band gap)
      bandY += BAND_GAP - PLANE_GAP;
    }
  }

  // GS row below — center ground stations relative to satellite grid width
  const gsY = bandY;
  const satGridWidth = maxSlotCount > 0 ? (maxSlotCount - 1) * NODE_SPACING_X : 0;
  const gsRowWidth = gss.length > 0 ? (gss.length - 1) * GS_SPACING : 0;
  const gsStartX = MARGIN + (satGridWidth - gsRowWidth) / 2;
  for (let i = 0; i < gss.length; i++) {
    const gs = gss[i]!;
    const x = gsStartX + i * GS_SPACING;
    const y = gsY;
    layoutNodes.push({
      id: gs.node_id,
      x,
      y,
      type: "ground_station",
      area: gs.routing_area,
      plane: null,
      slot: null,
    });

    // Ground stations sit in their own row — don't extend area bounds
    // to include them, as their wide x-spread would distort area boxes.
  }

  // Filter out areas that should not be drawn:
  // - skip when only one area exists
  // - skip areas with null, "unknown", or "0.0.0.0" ids
  const AREA_PAD = 16;
  const filteredAreaEntries = [...areaBoundsMap.entries()].filter(([id]) =>
    id !== "unknown" && id !== "0.0.0.0" && id !== "",
  );
  const areaBounds: AreaBounds[] = (areaBoundsMap.size <= 1 ? [] : filteredAreaEntries).map(([id, b]) => ({
    id,
    minX: b.minX - AREA_PAD,
    minY: b.minY - AREA_PAD,
    maxX: b.maxX + AREA_PAD,
    maxY: b.maxY + AREA_PAD,
  }));

  // Build layout links — include both active and recently-failed
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

  const maxX = Math.max(...layoutNodes.map((n) => n.x), 0) + MARGIN;
  const maxY = gsY + NODE_SPACING_Y + MARGIN;

  return { nodes: layoutNodes, links: layoutLinks, areas: areaBounds, width: maxX, height: maxY };
}
