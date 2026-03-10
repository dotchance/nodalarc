import type { ConsoleNode, GraphNode } from "../types";

export const CELL_W = 80;    // px between satellite columns (slots)
export const CELL_H = 64;    // px between satellite rows (planes)
export const MARGIN = 48;    // px left/top margin
export const GS_Y_OFFSET = 80;  // px below last satellite row for ground stations
export const GS_SPACING = 100;  // px between ground stations

/**
 * Compute (x, y) pixel positions for all nodes.
 *
 * Satellites: arranged by plane (row) x slot (column).
 *   x = MARGIN + slot * CELL_W
 *   y = MARGIN + plane * CELL_H
 *
 * Ground stations: sorted alphabetically, evenly spaced in a row below all
 * satellite planes.
 *   y = MARGIN + (maxPlane + 1) * CELL_H + GS_Y_OFFSET
 *   x = MARGIN + index * GS_SPACING  (centered if space allows)
 *
 * Returns a Map<node_id, GraphNode> for O(1) lookup by downstream code.
 */
export function computeLayout(nodes: ConsoleNode[]): Map<string, GraphNode> {
    const result = new Map<string, GraphNode>();

    const satellites = nodes
        .filter(n => n.node_type === "satellite" && n.plane != null && n.slot != null)
        .sort((a, b) => (a.plane! - b.plane!) || (a.slot! - b.slot!));

    const groundStations = nodes
        .filter(n => n.node_type === "ground_station")
        .sort((a, b) => a.node_id.localeCompare(b.node_id));

    const maxPlane = satellites.reduce((m, n) => Math.max(m, n.plane!), -1);

    for (const sat of satellites) {
        result.set(sat.node_id, {
            ...sat,
            x: MARGIN + sat.slot! * CELL_W,
            y: MARGIN + sat.plane! * CELL_H,
        });
    }

    const gsY = MARGIN + (maxPlane + 1) * CELL_H + GS_Y_OFFSET;
    const totalGsWidth = (groundStations.length - 1) * GS_SPACING;
    const gsStartX = MARGIN + Math.max(0, (satellites.reduce((m, n) => Math.max(m, n.slot!), 0) * CELL_W - totalGsWidth) / 2);

    groundStations.forEach((gs, i) => {
        result.set(gs.node_id, {
            ...gs,
            x: gsStartX + i * GS_SPACING,
            y: gsY,
        });
    });

    return result;
}

/**
 * Compute the SVG viewBox dimensions needed to contain the layout.
 */
export function computeViewBox(nodeMap: Map<string, GraphNode>): {
    width: number;
    height: number;
} {
    let maxX = 0;
    let maxY = 0;
    for (const node of nodeMap.values()) {
        if (node.x > maxX) maxX = node.x;
        if (node.y > maxY) maxY = node.y;
    }
    return { width: maxX + MARGIN * 2, height: maxY + MARGIN * 2 };
}

/** Routing area -> color, matching VF Section 10.1 */
const AREA_COLORS: Record<string, string> = {
    "49.0001": "#cc4444",
    "49.0002": "#44aa44",
    "49.0003": "#4477bb",
    "49.0004": "#cc8844",
};
const GROUND_COLOR = "#00d4aa";
const UNKNOWN_AREA_COLOR = "#666688";

export function areaColor(routing_area: string | null): string {
    if (routing_area === null) return GROUND_COLOR;
    return AREA_COLORS[routing_area] ?? UNKNOWN_AREA_COLOR;
}
