/** Ground station detail panel — uplinks, flows. */

import { translateReason } from "../translate";
import type { NodeState, StateSnapshot } from "../types";

interface GroundStationDetailProps {
  node: NodeState;
  snapshot: StateSnapshot;
}

export function GroundStationDetail({ node, snapshot }: GroundStationDetailProps) {
  const connectedLinks = snapshot.links.filter(
    (l) => l.node_a === node.node_id || l.node_b === node.node_id,
  );

  const flows = snapshot.active_flows.filter(
    (f) => f.src_node === node.node_id || f.dst_node === node.node_id,
  );

  return (
    <div>
      <h2>{node.node_id}</h2>
      <div className="detail-row">
        <span className="detail-label">Type</span>
        <span className="detail-value">Ground Station</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Position</span>
        <span className="detail-value">
          {node.lat_deg.toFixed(2)}° / {node.lon_deg.toFixed(2)}°
        </span>
      </div>

      <h3>Uplinks ({connectedLinks.length})</h3>
      {connectedLinks.map((l) => {
        const peer = l.node_a === node.node_id ? l.node_b : l.node_a;
        return (
          <div className="detail-row" key={`${l.node_a}:${l.node_b}`}>
            <span className="detail-label">{peer}</span>
            <span className="detail-value">
              {l.latency_ms.toFixed(1)}ms — {translateReason(l.link_reason)}
            </span>
          </div>
        );
      })}

      {flows.length > 0 && (
        <>
          <h3>Flows</h3>
          {flows.map((f) => (
            <div className="detail-row" key={f.flow_id}>
              <span className="detail-label">{f.flow_id}</span>
              <span className="detail-value">
                {f.src_node} → {f.dst_node}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
