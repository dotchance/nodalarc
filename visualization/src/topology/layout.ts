/** Deterministic grid layout for topology view.
 *  Routing areas as horizontal bands, satellites by slot index, GS row below.
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

export interface TopologyLayout {
  nodes: LayoutNode[];
  links: LayoutLink[];
  width: number;
  height: number;
}

const NODE_SPACING_X = 48;
const NODE_SPACING_Y = 48;
const BAND_GAP = 32;
const MARGIN = 40;

export function computeLayout(
  nodes: NodeState[],
  links: LinkState[],
): TopologyLayout {
  const sats = nodes.filter((n) => n.node_type === "satellite");
  const gss = nodes.filter((n) => n.node_type === "ground_station");

  // Group satellites by area
  const areaMap = new Map<string, NodeState[]>();
  for (const sat of sats) {
    const area = sat.routing_area ?? "unknown";
    if (!areaMap.has(area)) areaMap.set(area, []);
    areaMap.get(area)!.push(sat);
  }

  // Sort areas for deterministic order
  const sortedAreas = [...areaMap.keys()].sort();

  const layoutNodes: LayoutNode[] = [];
  let bandY = MARGIN;

  for (const area of sortedAreas) {
    const areaSats = areaMap.get(area)!;
    // Sort by plane then slot for deterministic layout
    areaSats.sort((a, b) => {
      if ((a.plane ?? 0) !== (b.plane ?? 0)) return (a.plane ?? 0) - (b.plane ?? 0);
      return (a.slot ?? 0) - (b.slot ?? 0);
    });

    for (let i = 0; i < areaSats.length; i++) {
      const sat = areaSats[i]!;
      layoutNodes.push({
        id: sat.node_id,
        x: MARGIN + i * NODE_SPACING_X,
        y: bandY,
        type: "satellite",
        area: sat.routing_area,
        plane: sat.plane,
        slot: sat.slot,
      });
    }

    bandY += NODE_SPACING_Y + BAND_GAP;
  }

  // GS row below
  const gsY = bandY;
  for (let i = 0; i < gss.length; i++) {
    const gs = gss[i]!;
    layoutNodes.push({
      id: gs.node_id,
      x: MARGIN + i * NODE_SPACING_X,
      y: gsY,
      type: "ground_station",
      area: null,
      plane: null,
      slot: null,
    });
  }

  // Build layout links
  const nodeAreaMap = new Map<string, string | null>();
  for (const n of nodes) {
    nodeAreaMap.set(n.node_id, n.routing_area);
  }

  const layoutLinks: LayoutLink[] = links
    .filter((l) => l.state === "active")
    .map((l) => ({
      nodeA: l.node_a,
      nodeB: l.node_b,
      state: l.state,
      isGround: l.node_a.startsWith("gs-") || l.node_b.startsWith("gs-"),
      isCrossArea: nodeAreaMap.get(l.node_a) !== nodeAreaMap.get(l.node_b),
    }));

  const maxX = Math.max(...layoutNodes.map((n) => n.x), 0) + MARGIN;
  const maxY = gsY + NODE_SPACING_Y + MARGIN;

  return { nodes: layoutNodes, links: layoutLinks, width: maxX, height: maxY };
}
