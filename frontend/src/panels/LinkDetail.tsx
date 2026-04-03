// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Link detail panel — state, metrics, flow paths, history. */

import { useEffect, useState } from "react";
import { translateReason, translateLinkType } from "../translate";
import { REST_URL } from "../config";
import type { LinkState, StateSnapshot } from "../types";

interface LinkDetailProps {
  link: LinkState;
  snapshot: StateSnapshot;
}

interface LinkHistoryEntry {
  sim_time: string;
  event_type: string;
  reason: string;
  node_a: string;
  node_b: string;
}

export function LinkDetail({ link, snapshot }: LinkDetailProps) {
  const [history, setHistory] = useState<LinkHistoryEntry[]>([]);

  // Fetch link history from REST API on select
  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const res = await fetch(
          `${REST_URL}/api/v1/links?start=&end=`,
        );
        if (res.ok) {
          const data = (await res.json()) as LinkHistoryEntry[];
          // Filter to this link's nodes
          const filtered = data.filter(
            (e) =>
              (e.node_a === link.node_a && e.node_b === link.node_b) ||
              (e.node_a === link.node_b && e.node_b === link.node_a),
          );
          setHistory(filtered.slice(-20));
        }
      } catch {
        // Ignore fetch errors
      }
    };
    fetchHistory();
  }, [link.node_a, link.node_b]);

  // Find flows traversing this link
  const flowsOnLink = snapshot.traced_paths.filter((tp) => {
    for (let i = 0; i < tp.hops.length - 1; i++) {
      const a = tp.hops[i]!;
      const b = tp.hops[i + 1]!;
      if (
        (a === link.node_a && b === link.node_b) ||
        (a === link.node_b && b === link.node_a)
      ) {
        return true;
      }
    }
    return false;
  });

  return (
    <div>
      <h2>Link: {link.node_a} ↔ {link.node_b}</h2>
      <div className="detail-row">
        <span className="detail-label">State</span>
        <span className={`detail-value detail-value--${link.state === "active" ? "active" : "failed"}`}>
          {link.state}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Reason</span>
        <span className="detail-value">{translateReason(link.link_reason)}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Type</span>
        <span className="detail-value">{translateLinkType(link.link_type)}</span>
      </div>

      <h3>Metrics</h3>
      <div className="detail-row">
        <span className="detail-label">Latency</span>
        <span className="detail-value">{link.latency_ms.toFixed(1)} ms</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Bandwidth</span>
        <span className="detail-value">{link.bandwidth_mbps.toFixed(0)} Mbps</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Range</span>
        <span className="detail-value">{link.range_km.toFixed(0)} km</span>
      </div>
      {link.traffic_load_pct != null && (
        <div className="detail-row">
          <span className="detail-label">Load</span>
          <span className="detail-value">{link.traffic_load_pct.toFixed(1)}%</span>
        </div>
      )}

      {flowsOnLink.length > 0 && (
        <>
          <h3>Flow Paths</h3>
          {flowsOnLink.map((tp) => (
            <div className="detail-row" key={tp.flow_id}>
              <span className="detail-label">{tp.flow_id}</span>
              <span className="detail-value">{tp.hops.length} hops</span>
            </div>
          ))}
        </>
      )}

      {history.length > 0 && (
        <>
          <h3>History (last {history.length})</h3>
          {history.map((h, i) => (
            <div className="detail-row" key={i}>
              <span className="detail-label" style={{ fontSize: 10 }}>
                {h.sim_time?.substring(11, 19) ?? ""}
              </span>
              <span className="detail-value" style={{ fontSize: 10 }}>
                {h.event_type} — {translateReason(h.reason)}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
