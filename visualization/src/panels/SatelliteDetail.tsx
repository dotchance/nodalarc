/** Satellite detail panel — routing info, adjacencies, position. */

import { translateReason } from "../translate";
import { areaCSSColor } from "../globe/colors";
import type { NodeState, StateSnapshot } from "../types";

interface SatelliteDetailProps {
  node: NodeState;
  snapshot: StateSnapshot;
}

export function SatelliteDetail({ node, snapshot }: SatelliteDetailProps) {
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

  return (
    <div>
      <h2>{node.node_id}</h2>
      <div className="detail-row">
        <span className="detail-label">Type</span>
        <span className="detail-value">Satellite</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Plane / Slot</span>
        <span className="detail-value">P{node.plane ?? "?"} / S{node.slot ?? "?"}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Routing Area</span>
        <span className="detail-value" style={{ color: areaCSSColor(node.routing_area) }}>
          {node.routing_area ?? "none"}
        </span>
      </div>

      <h3>Position</h3>
      <div className="detail-row">
        <span className="detail-label">Lat / Lon</span>
        <span className="detail-value">
          {node.lat_deg.toFixed(2)}° / {node.lon_deg.toFixed(2)}°
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Altitude</span>
        <span className="detail-value">{node.alt_km.toFixed(1)} km</span>
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
            return (
              <div className="detail-row" key={`${l.node_a}:${l.node_b}`}>
                <span className="detail-label">{peer}</span>
                <span className="detail-value">
                  {l.latency_ms.toFixed(1)}ms
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
            return (
              <div className="detail-row" key={`${l.node_a}:${l.node_b}`}>
                <span className="detail-label">{peer}</span>
                <span className="detail-value">
                  {translateReason(l.link_reason)}
                </span>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
