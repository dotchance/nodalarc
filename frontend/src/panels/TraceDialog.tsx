// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Trace path dialog — continuous live traceroute in both directions, side by
 *  side, with per-hop round trips and how each direction ended.
 */

import { useState, useEffect, useMemo, useCallback } from "react";
import { REST_URL, authHeaders } from "../config";
import type { NodeState, StateSnapshot, TraceState, TraceStopReason } from "../types";
import { isGroundNode } from "../networkIdentity";
import { apiErrorFromException, apiErrorMessage } from "../ui/apiError";

/** Why a stopped trace stopped, as the dialog says it. */
const STOP_REASON_TEXT: Record<TraceStopReason, string> = {
  time_limit: "STOPPED · time limit reached",
  internal_error: "STOPPED · internal error",
};

interface TraceDialogProps {
  nodes: NodeState[];
  selectedNodeId?: string | null;
  snapshot?: StateSnapshot | null;
}

/** What a direction's outcome says beyond its hop list, or null when it reached. */
function directionOutcome(
  label: string,
  state: TraceState,
  hopCount: number,
  error: string | null,
): string | null {
  if (state === "running") return `${label}: tracing…`;
  if (state === "failed") return `${label} trace could not run: ${error ?? ""}`;
  if (state === "not_reached") {
    return `${label}: destination did not answer after ${hopCount - 1} hop${hopCount - 1 === 1 ? "" : "s"}`;
  }
  return null;
}

export function TraceDialog({ nodes, selectedNodeId, snapshot }: TraceDialogProps) {
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [continuous, setContinuous] = useState(false);

  useEffect(() => {
    if (selectedNodeId) setSrc(selectedNodeId);
  }, [selectedNodeId]);

  const sorted = [...nodes].sort((a, b) => {
    if (a.node_type !== b.node_type) return a.node_type === "ground_station" ? -1 : 1;
    return a.node_id.localeCompare(b.node_id);
  });
  const nodesById = useMemo(() => new Map(nodes.map((node) => [node.node_id, node])), [nodes]);

  // Get continuous trace result from WebSocket snapshot. The continuous trace
  // is a single server-side singleton, so its presence here is the source of
  // truth for "a trace is running" — NOT the local `continuous` flag, which is
  // lost when this dialog unmounts (navigating to another node and back). A
  // trace started earlier keeps rendering in the globe, so the Stop control
  // must appear whenever the server reports an active trace, from any view.
  const tp = snapshot?.traced_paths?.find(p => p.flow_id === "__continuous_trace__") ?? null;
  // A result that stopped (at the time limit or on an error) stays shown and
  // the Trace control returns, with the same source and destination.
  const isTracing = tp != null ? tp.tracing : continuous;

  // Reflect the running trace's endpoints so the user sees what they're about
  // to stop even when they returned to this dialog fresh.
  useEffect(() => {
    if (tp) { setSrc(tp.src_node); setDst(tp.dst_node); }
  }, [tp?.src_node, tp?.dst_node]);

  const handleTrace = useCallback(async () => {
    if (!src || !dst || src === dst) return;
    setLoading(true); setError(null);
    try {
      const res = await fetch(`${REST_URL}/api/v1/trace/start`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ src_node: src, dst_node: dst }),
      });
      if (res.ok) {
        setContinuous(true);
      } else {
        setError(await apiErrorMessage(res));
      }
    } catch (err) {
      setError(apiErrorFromException(err));
    } finally {
      setLoading(false);
    }
  }, [src, dst]);

  const handleStop = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(`${REST_URL}/api/v1/trace/stop`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (!res.ok) {
        setError(await apiErrorMessage(res));
        return;
      }
      setContinuous(false);
    } catch (err) {
      setError(apiErrorFromException(err));
    }
  }, []);

  return (
    <div className="trace-dialog">
      <h3>Trace Path</h3>
      <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
        <select value={src} onChange={e => setSrc(e.target.value)} style={{ flex: 1 }}>
          <option value="">Source...</option>
          {sorted.map(n => <option key={n.node_id} value={n.node_id}>{n.node_id}</option>)}
        </select>
        <select value={dst} onChange={e => setDst(e.target.value)} style={{ flex: 1 }}>
          <option value="">Destination...</option>
          {sorted.map(n => <option key={n.node_id} value={n.node_id}>{n.node_id}</option>)}
        </select>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        {!isTracing ? (
          <button className="trace-button" onClick={handleTrace} disabled={loading || !src || !dst || src === dst}>
            {loading ? "Starting..." : "Trace"}
          </button>
        ) : (
          <button className="trace-button trace-button--stop" onClick={handleStop}>
            Stop Trace
          </button>
        )}
      </div>

      {error && <div className="trace-error">{error}</div>}

      {/* Live and stopped trace results */}
      {tp && (
        <div style={{ marginTop: 8 }}>
          {/* Summary line */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            {tp.tracing ? (
              <span className="trace-live">LIVE</span>
            ) : (
              <span className="trace-warn">
                {tp.stop_reason ? STOP_REASON_TEXT[tp.stop_reason] : "STOPPED"}
              </span>
            )}
            <span style={{ fontSize: 11, color: "var(--text-primary)", fontWeight: 600 }}>
              {tp.hops.length} hops
              {tp.rtt_ms != null && ` · ${tp.rtt_ms.toFixed(1)}ms fwd`}
              {tp.reverse_rtt_ms != null && ` / ${tp.reverse_rtt_ms.toFixed(1)}ms rev`}
            </span>
          </div>

          {tp.asymmetry_detected === true && (
            <div className="trace-warn">Path asymmetry detected</div>
          )}

          {[
            directionOutcome("Forward", tp.state, tp.hops.length, tp.error),
            directionOutcome("Reverse", tp.reverse_state, tp.reverse_hops.length, tp.reverse_error),
          ]
            .filter((line): line is string => line !== null)
            .map((line) => (
              <div key={line} className="trace-warn trace-warn--block">{line}</div>
            ))}

          {/* Side-by-side forward + reverse */}
          <div style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 9, fontWeight: 600, color: "var(--text-dim)", textTransform: "uppercase" as const, letterSpacing: "0.05em", marginBottom: 4 }}>Forward</div>
              <HopList hops={tp.hops} hopRtts={tp.hop_rtts} nodesById={nodesById} />
            </div>
            {tp.reverse_hops.length > 0 && (
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 9, fontWeight: 600, color: "var(--text-dim)", textTransform: "uppercase" as const, letterSpacing: "0.05em", marginBottom: 4 }}>Reverse</div>
                <HopList hops={tp.reverse_hops} hopRtts={tp.reverse_hop_rtts} nodesById={nodesById} />
              </div>
            )}
          </div>
        </div>
      )}

      {isTracing && !tp && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-dim)" }}>Tracing path...</div>
      )}
    </div>
  );
}

/** Render a hop list with per-hop latency from real cumulative RTT data. */
function HopList({
  hops,
  hopRtts,
  nodesById,
}: {
  hops: string[];
  hopRtts: (number | null)[];
  nodesById: ReadonlyMap<string, NodeState>;
}) {
  return (
    <div className="trace-hops">
      {hops.map((hop, i) => {
        const node = nodesById.get(hop);
        const isGS = node ? isGroundNode(node) : false;
        const rtt = hopRtts[i] ?? null;
        // Per-hop delay = delta between consecutive cumulative traceroute round
        // trips. Row 1 is the trace's source, whose round trip to itself is zero.
        const prevRtt = i === 1 ? 0 : i > 1 ? (hopRtts[i - 1] ?? null) : null;
        const delta = rtt != null && prevRtt != null ? rtt - prevRtt : null;

        return (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
            <span style={{ color: "var(--text-dim)", width: 16, textAlign: "right", flexShrink: 0 }}>{i + 1}</span>
            <span className={`trace-hop-name ${isGS ? "trace-hop-name--ground" : "trace-hop-name--sat"}`}>{hop}</span>
            {delta != null && delta > 0 && (
              <span style={{ color: "var(--text-secondary)", fontSize: 9, flexShrink: 0 }}>
                {delta.toFixed(1)}ms
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
