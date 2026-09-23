// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Link detail panel — state, metrics, flow paths, history. */

import { useEffect, useState } from "react";
import { translateLinkType } from "../translate";
import { linkEventLabel } from "../explain/linkEvents";
import { REST_URL, authHeaders } from "../config";
import { apiErrorFromException, apiErrorMessage } from "../ui/apiError";
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

/** A terminal transmit rate, keeping a fractional declared rate such as 23.6 Mbps. */
function formatRate(mbps: number): string {
  return `${Number.isInteger(mbps) ? mbps.toFixed(0) : mbps.toFixed(1)} Mbps`;
}

export function LinkDetail({ link, snapshot }: LinkDetailProps) {
  const [history, setHistory] = useState<LinkHistoryEntry[]>([]);
  // Why no history is shown: recording is off for the session, recording
  // failed, or the request failed. Stated, never shown as an empty history.
  const [historyUnavailable, setHistoryUnavailable] = useState<string | null>(null);

  // Fetch the recorded link history of this link's first node on select.
  useEffect(() => {
    let current = true;
    const fetchHistory = async () => {
      setHistory([]);
      setHistoryUnavailable(null);
      try {
        const res = await fetch(
          `${REST_URL}/api/v1/links?node=${encodeURIComponent(link.node_a)}`,
          { headers: authHeaders() },
        );
        if (!res.ok) {
          const message = await apiErrorMessage(res);
          if (current) setHistoryUnavailable(message);
          return;
        }
        const data = (await res.json()) as LinkHistoryEntry[];
        const onThisLink = data.filter(
          (e) =>
            (e.node_a === link.node_a && e.node_b === link.node_b) ||
            (e.node_a === link.node_b && e.node_b === link.node_a),
        );
        if (current) setHistory(onThisLink.slice(-20));
      } catch (err) {
        if (current) setHistoryUnavailable(apiErrorFromException(err));
      }
    };
    fetchHistory();
    return () => {
      current = false;
    };
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
        <span className="detail-value">{linkEventLabel(link.link_reason)}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Type</span>
        <span className="detail-value">{translateLinkType(link.link_type)}</span>
      </div>
      {link.link_rule_id && (
        <div className="detail-row">
          <span className="detail-label">Rule</span>
          <span className="detail-value">{link.link_rule_id}</span>
        </div>
      )}
      {link.topology_mode && (
        <div className="detail-row">
          <span className="detail-label">Topology</span>
          <span className="detail-value">{link.topology_mode}</span>
        </div>
      )}
      {link.endpoint_segments && (
        <div className="detail-row">
          <span className="detail-label">Segments</span>
          <span className="detail-value">{link.endpoint_segments.join(" ↔ ")}</span>
        </div>
      )}

      <h3>Metrics</h3>
      <div className="detail-row">
        <span className="detail-label">Latency</span>
        <span className="detail-value">{link.latency_ms.toFixed(1)} ms</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">{link.node_a} → {link.node_b}</span>
        <span className="detail-value">{formatRate(link.transmit_mbps_a)}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">{link.node_b} → {link.node_a}</span>
        <span className="detail-value">{formatRate(link.transmit_mbps_b)}</span>
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

      {historyUnavailable !== null && (
        <>
          <h3>History</h3>
          <div className="detail-row">
            <span className="detail-value">{historyUnavailable}</span>
          </div>
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
                {h.event_type} — {linkEventLabel(h.reason)}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
