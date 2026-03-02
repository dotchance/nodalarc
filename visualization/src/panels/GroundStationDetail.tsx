/** Ground station detail panel — role, area, terminals, uplinks, flows. */

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

  // Count ground link terminals (active links = terminals in use)
  const terminalCount = connectedLinks.length;

  return (
    <div>
      <h2>{node.node_id}</h2>
      <div className="detail-row">
        <span className="detail-label">Role</span>
        <span className="detail-value">Gateway</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Area</span>
        <span className="detail-value">ground</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Terminals</span>
        <span className="detail-value">
          {terminalCount} OGT ({terminalCount}/{terminalCount} in use)
        </span>
      </div>

      <h3>Uplinks ({connectedLinks.length})</h3>
      {connectedLinks.map((l) => {
        const peer = l.node_a === node.node_id ? l.node_b : l.node_a;
        return (
          <div className="detail-row" key={`${l.node_a}:${l.node_b}`}>
            <span className="detail-label">{peer}</span>
            <span className="detail-value">
              {l.state === "active" ? "UP" : "DOWN"} {l.latency_ms.toFixed(1)}ms
            </span>
          </div>
        );
      })}

      {flows.length > 0 && (
        <>
          <h3>Flows</h3>
          {flows.map((f) => {
            // Find traced path for this flow
            const trace = snapshot.traced_paths.find((t) => t.flow_id === f.flow_id);
            return (
              <div className="detail-row" key={f.flow_id}>
                <span className="detail-label">{f.flow_id}</span>
                <span className="detail-value">
                  {f.src_node} → {f.dst_node}
                  {trace ? ` (${trace.hops.length} hops)` : ""}
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
    </div>
  );
}
