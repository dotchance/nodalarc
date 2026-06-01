// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Ground station detail panel — role, area, terminals, uplinks, flows. */

import { useEffect, useState } from "react";
import { REST_URL } from "../config";
import type { NodeState, StateSnapshot, Selection } from "../types";
import { useDecisionExplanation } from "../explain/useDecisionExplanation";
import { useDecisionTimeline } from "../explain/useDecisionTimeline";
import { fetchGroundDecisions, type GroundDecisionsSnapshot } from "../explain/client";
import { candidateStatus } from "../explain/derive";
import { CandidateRow } from "../explain/components/CandidateRow";
import { GroundStationCard } from "../explain/components/GroundStationCard";
import { PairInspectorView } from "../explain/components/PairInspectorView";

interface GroundStationDetailProps {
  node: NodeState;
  snapshot: StateSnapshot;
  onSelect: (sel: Selection | null) => void;
}

const _ordered = (a: string, b: string): string => [a, b].sort().join("|");

export function GroundStationDetail({ node, snapshot, onSelect }: GroundStationDetailProps) {
  const [tracingFlow, setTracingFlow] = useState<string | null>(null);
  const [inspectedSat, setInspectedSat] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<GroundDecisionsSnapshot | null>(null);
  const explanation = useDecisionExplanation(node.node_id);
  const decisionTimeline = useDecisionTimeline(node.node_id);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    // Clear the previous GS's slice immediately so a stale cross-node candidate never
    // renders for a moment after switching stations.
    setDecisions(null);
    const load = async () => {
      try {
        const snap = await fetchGroundDecisions(node.node_id, controller.signal);
        if (alive) setDecisions(snap);
      } catch {
        // candidate list is non-essential; leave prior state on transient errors
      }
    };
    void load();
    const timer = window.setInterval(load, 2000);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [node.node_id]);

  // Reset the inspected pair when the selected GS changes.
  useEffect(() => {
    setInspectedSat(null);
  }, [node.node_id]);

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

  if (inspectedSat) {
    return (
      <PairInspectorView
        gsId={node.node_id}
        satId={inspectedSat}
        onBack={() => setInspectedSat(null)}
      />
    );
  }

  const withheld = new Set(
    (decisions?.unscheduled_pairs ?? []).map((u) => _ordered(u.pair[0], u.pair[1])),
  );
  const unschedReason = new Map(
    (decisions?.unscheduled_pairs ?? []).map((u) => [
      _ordered(u.pair[0], u.pair[1]),
      u.unscheduled_reason,
    ]),
  );
  // The server already slices to this GS's pairs (?node=), so every decision involves it.
  const candidates = (decisions?.decisions ?? [])
    .map((d) => {
      const sat = d.pair[0] === node.node_id ? d.pair[1] : d.pair[0];
      const key = _ordered(d.pair[0], d.pair[1]);
      const status = candidateStatus({
        visible: d.visible,
        isWithheld: withheld.has(key),
        rejectReason: d.reject_reason,
        unscheduledReason: unschedReason.get(key) ?? null,
      });
      return { sat, d, status };
    });

  return (
    <div>
      <h2>{node.node_id}</h2>
      {explanation.facts ? (
        <GroundStationCard facts={explanation.facts} timeline={decisionTimeline.timeline} />
      ) : null}
      {candidates.length > 0 ? (
        <>
          <h3>Candidates ({candidates.length})</h3>
          {candidates.map(({ sat, d, status }) => (
            <CandidateRow
              key={sat}
              node={sat}
              family={status.family}
              label={status.label}
              detail={d.elevation_deg != null ? `${Math.round(d.elevation_deg)} deg el` : null}
              onClick={() => setInspectedSat(sat)}
            />
          ))}
        </>
      ) : null}
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
