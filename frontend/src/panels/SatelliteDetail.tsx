// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Satellite detail panel — role, area, adjacencies, position. */

import { translateReason } from "../translate";
import { areaCSSColor } from "../globe/colors";
import type { NodeState, StateSnapshot, Selection } from "../types";

interface SatelliteDetailProps {
  node: NodeState;
  snapshot: StateSnapshot;
  onSelect: (sel: Selection | null) => void;
}

function linkTypeLabel(linkType: string | null): string {
  switch (linkType) {
    case "intra_plane_isl": return "intra-area";
    case "cross_plane_isl": return "cross-area";
    case "ground_uplink": return "ground";
    case "ground_downlink": return "ground";
    default: return linkType ?? "unknown";
  }
}

export function SatelliteDetail({ node, snapshot, onSelect }: SatelliteDetailProps) {
  // Find connected links
  const connectedLinks = snapshot.links.filter(
    (l) => l.node_a === node.node_id || l.node_b === node.node_id,
  );
  const islLinks = connectedLinks.filter(
    (l) => !l.node_a.startsWith("gs-") && !l.node_b.startsWith("gs-"),
  );
  const gndLinks = connectedLinks.filter(
    (l) => l.node_a.startsWith("gs-") || l.node_b.startsWith("gs-"),
  );

  // Determine role: Router vs Router (ABR)
  const linkedAreas = new Set<string>();
  for (const l of connectedLinks) {
    const peerNode = snapshot.nodes.find(
      (n) => n.node_id === (l.node_a === node.node_id ? l.node_b : l.node_a),
    );
    if (peerNode?.routing_area) linkedAreas.add(peerNode.routing_area);
  }
  if (node.routing_area) linkedAreas.add(node.routing_area);
  const role = linkedAreas.size > 1 ? "Router (ABR)" : "Router";

  const selectPeer = (peerId: string) => {
    const type = peerId.startsWith("gs-") ? "ground_station" : "satellite";
    onSelect({ type, id: peerId });
  };

  const selectLink = (nodeA: string, nodeB: string) => {
    const key = nodeA < nodeB ? `${nodeA}:${nodeB}` : `${nodeB}:${nodeA}`;
    onSelect({ type: "link", id: key });
  };

  return (
    <div>
      <h2>{node.node_id}</h2>
      <div className="detail-row">
        <span className="detail-label">Role</span>
        <span className="detail-value">{role}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Routing Area</span>
        <span className="detail-value" style={{ color: areaCSSColor(node.routing_area) }}>
          {node.routing_area ?? "none"}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Plane / Slot</span>
        <span className="detail-value">P{node.plane ?? "?"} / S{node.slot ?? "?"}</span>
      </div>

      <h3>Adjacencies ({node.neighbor_count})</h3>
      <div className="detail-row">
        <span className="detail-label">ISL links</span>
        <span className="detail-value">{node.isl_count}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Ground links</span>
        <span className="detail-value">{node.gnd_count}</span>
      </div>

      {islLinks.length > 0 && (
        <>
          <h3>ISL Links</h3>
          {islLinks.map((l) => {
            const peer = l.node_a === node.node_id ? l.node_b : l.node_a;
            const localIface = l.node_a === node.node_id ? l.interface_a : l.interface_b;
            const stateUp = l.state === "active";
            return (
              <div className="detail-row detail-row--clickable" key={`${l.node_a}:${l.node_b}`}>
                <span
                  className="detail-label detail-label--link"
                  onClick={() => selectPeer(peer)}
                  title={`Select ${peer}`}
                >
                  {localIface ? `${localIface}: ` : ""}{peer}
                </span>
                <span
                  className="detail-value"
                  onClick={() => selectLink(l.node_a, l.node_b)}
                  title="Select link"
                >
                  <span className={stateUp ? "link-state--up" : "link-state--down"}>
                    {stateUp ? "UP" : "DOWN"}
                  </span>
                  {" "}{l.latency_ms.toFixed(1)}ms {linkTypeLabel(l.link_type)}
                  {!stateUp && l.link_reason ? ` — ${translateReason(l.link_reason)}` : ""}
                </span>
              </div>
            );
          })}
        </>
      )}

      {gndLinks.length > 0 && (
        <>
          <h3>Ground Links</h3>
          {gndLinks.map((l) => {
            const peer = l.node_a === node.node_id ? l.node_b : l.node_a;
            const localIface = l.node_a === node.node_id ? l.interface_a : l.interface_b;
            return (
              <div className="detail-row detail-row--clickable" key={`${l.node_a}:${l.node_b}`}>
                <span
                  className="detail-label detail-label--link"
                  onClick={() => selectPeer(peer)}
                  title={`Select ${peer}`}
                >
                  {localIface ? `${localIface}: ` : ""}{peer}
                </span>
                <span
                  className="detail-value"
                  onClick={() => selectLink(l.node_a, l.node_b)}
                  title="Select link"
                >
                  {l.latency_ms.toFixed(1)}ms — {translateReason(l.link_reason)}
                </span>
              </div>
            );
          })}
        </>
      )}

      <h3>Position</h3>
      <div className="detail-row">
        <span className="detail-label">Lat / Lon</span>
        <span className="detail-value">
          {node.lat_deg.toFixed(2)}&deg; / {node.lon_deg.toFixed(2)}&deg;
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Altitude</span>
        <span className="detail-value">{node.alt_km.toFixed(1)} km</span>
      </div>
    </div>
  );
}
