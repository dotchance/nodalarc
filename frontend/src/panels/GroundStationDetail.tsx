// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
/** Ground station detail panel — role, area, terminals, uplinks, flows. */

import { useState } from "react";
import { REST_URL } from "../config";
import type { NodeState, StateSnapshot, Selection } from "../types";

interface GroundStationDetailProps {
  node: NodeState;
  snapshot: StateSnapshot;
  onSelect: (sel: Selection | null) => void;
}

export function GroundStationDetail({ node, snapshot, onSelect }: GroundStationDetailProps) {
  const [tracingFlow, setTracingFlow] = useState<string | null>(null);

  const connectedLinks = snapshot.links.filter(
    (l) => l.node_a === node.node_id || l.node_b === node.node_id,
  );

  const flows = snapshot.active_flows.filter(
    (f) => f.src_node === node.node_id || f.dst_node === node.node_id,
  );

  // Count ground link terminals (active links = terminals in use)
  const terminalCount = connectedLinks.length;

  const selectPeer = (peerId: string) => {
    const type = peerId.startsWith("gs-") ? "ground_station" : "satellite";
    onSelect({ type, id: peerId });
  };

  const traceFlow = async (srcNode: string, dstNode: string) => {
    setTracingFlow(`${srcNode}:${dstNode}`);
    try {
      await fetch(`${REST_URL}/api/v1/trace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src_node: srcNode, dst_node: dstNode }),
      });
    } catch {
      // trace errors are non-fatal
    }
    setTracingFlow(null);
  };

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
      {node.prefix && (
        <div className="detail-row">
          <span className="detail-label">Prefix</span>
          <span className="detail-value">{node.prefix}</span>
        </div>
      )}
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
          <div className="detail-row detail-row--clickable" key={`${l.node_a}:${l.node_b}`}>
            <span
              className="detail-label detail-label--link"
              onClick={() => selectPeer(peer)}
              title={`Select ${peer}`}
            >
              {peer}
            </span>
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
            const isTracing = tracingFlow === `${f.src_node}:${f.dst_node}`;
            return (
              <div className="detail-row" key={f.flow_id}>
                <span className="detail-label">{f.flow_id}</span>
                <span className="detail-value">
                  {f.src_node} → {f.dst_node}
                  {trace ? ` (${trace.hops.length} hops, ${trace.hops.length > 1 ? `${(trace.hops.length * 2).toFixed(0)}ms` : ""})` : ""}
                  <button
                    className="trace-btn"
                    onClick={() => traceFlow(f.src_node, f.dst_node)}
                    disabled={isTracing}
                    title="Trace this flow path"
                  >
                    {isTracing ? "..." : "Trace"}
                  </button>
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
