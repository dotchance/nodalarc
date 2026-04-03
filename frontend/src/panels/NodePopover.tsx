// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Floating popover showing selected node info on the globe view. */

import type { MutableRefObject } from "react";
import type { StateSnapshot, Selection } from "../types";
import type { GlobeActions } from "../globe/GlobeView";

interface NodePopoverProps {
  snapshot: StateSnapshot | null;
  selection: Selection;
  onClose: () => void;
  onOpenCli: () => void;
  globeActionsRef: MutableRefObject<GlobeActions | null>;
}

const AREA_COLORS: Record<string, string> = {
  "49.0001": "#cc4444", "49.0002": "#44aa44", "49.0003": "#4477bb", "49.0004": "#cc8844",
  "0.0.0.0": "#cc4444", "0.0.0.1": "#44aa44", "0.0.0.2": "#4477bb", "0.0.0.3": "#cc8844",
};

export function NodePopover({ snapshot, selection, onClose, onOpenCli }: NodePopoverProps) {
  const node = snapshot?.nodes.find((n) => n.node_id === selection.id) ?? null;

  const connectedLinks = (node && snapshot) ? snapshot.links.filter(
    (l) => l.node_a === node.node_id || l.node_b === node.node_id,
  ) : [];
  const islCount = connectedLinks.filter(
    (l) => !l.node_a.startsWith("gs-") && !l.node_b.startsWith("gs-"),
  ).length;
  const gndCount = connectedLinks.filter(
    (l) => l.node_a.startsWith("gs-") || l.node_b.startsWith("gs-"),
  ).length;

  let role = "";
  if (node) {
    const linkedAreas = new Set<string>();
    for (const l of connectedLinks) {
      const peer = snapshot!.nodes.find(
        (n) => n.node_id === (l.node_a === node.node_id ? l.node_b : l.node_a),
      );
      if (peer?.routing_area) linkedAreas.add(peer.routing_area);
    }
    if (node.routing_area) linkedAreas.add(node.routing_area);
    role = node.node_type === "ground_station"
      ? "Gateway"
      : linkedAreas.size > 1 ? "Router (ABR)" : "Router";
  }

  const areaColor = node?.routing_area ? (AREA_COLORS[node.routing_area] ?? "#aabbcc") : "#aabbcc";

  return (
    <div style={{
      position: "absolute", bottom: 12, left: 60, zIndex: 20,
      background: "rgba(26,26,46,0.94)", backdropFilter: "blur(4px)",
      color: "#e0e0e0", padding: "10px 12px", fontSize: 12, borderRadius: 6,
      width: 240, border: "1px solid #2a2a4e",
      boxShadow: "0 4px 16px rgba(0,0,0,0.4)", pointerEvents: "auto",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{node?.node_id ?? selection.id}</span>
        <button
          onClick={onClose}
          style={{ background: "none", border: "none", color: "#555577", fontSize: 14, cursor: "pointer", padding: "0 2px", lineHeight: 1 }}
        >✕</button>
      </div>
      {node ? (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", padding: "1px 0", fontSize: 11 }}>
            <span style={{ color: "#888899" }}>Type</span>
            <span>{role}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", padding: "1px 0", fontSize: 11 }}>
            <span style={{ color: "#888899" }}>Routing Area</span>
            <span style={{ color: areaColor }}>{node.routing_area ?? "none"}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", padding: "1px 0", fontSize: 11 }}>
            <span style={{ color: "#888899" }}>Neighbors</span>
            <span>{islCount} ISL, {gndCount} GND</span>
          </div>
          <button
            onClick={onOpenCli}
            style={{
              display: "block", width: "100%", marginTop: 8, padding: "5px 0",
              background: "transparent", border: "1px solid #4488ff", borderRadius: 4,
              color: "#4488ff", fontSize: 11, fontWeight: 600, cursor: "pointer", textAlign: "center",
            }}
          >Open CLI</button>
        </>
      ) : (
        <div style={{ color: "#ff3333" }}>Node not in snapshot</div>
      )}
    </div>
  );
}
