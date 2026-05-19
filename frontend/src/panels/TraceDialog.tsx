// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Trace path dialog — continuous live trace with side-by-side forward/reverse,
 *  per-hop latency, netem delays, and path validity countdown.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { REST_URL, authHeaders } from "../config";
import type { NodeState, TracedPath, StateSnapshot } from "../types";

interface TraceDialogProps {
  nodes: NodeState[];
  selectedNodeId?: string | null;
  onTraceResult?: (path: TracedPath | null) => void;
  snapshot?: StateSnapshot | null;
}

export function TraceDialog({ nodes, selectedNodeId, onTraceResult, snapshot }: TraceDialogProps) {
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [continuous, setContinuous] = useState(false);
  const [countdown, setCountdown] = useState<string | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (selectedNodeId) setSrc(selectedNodeId);
  }, [selectedNodeId]);

  const sorted = [...nodes].sort((a, b) => {
    if (a.node_type !== b.node_type) return a.node_type === "ground_station" ? -1 : 1;
    return a.node_id.localeCompare(b.node_id);
  });

  // Get continuous trace result from WebSocket snapshot
  const tp = snapshot?.traced_paths?.find(p => p.flow_id === "__continuous_trace__") ?? null;

  // Pass to parent for globe/topo rendering
  useEffect(() => {
    if (continuous && tp) onTraceResult?.(tp);
  }, [continuous, tp, onTraceResult]);

  // Countdown from path_valid_seconds — sim-time delta that resets each trace.
  // Snapshot the value and wall-clock arrival time, tick down by elapsed wall time.
  useEffect(() => {
    if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }
    const secs = tp?.path_valid_seconds;
    if (secs == null || secs <= 0) {
      setCountdown(secs != null && secs <= 0 ? "Path change expected" : null);
      return;
    }
    const startWall = Date.now();
    const tick = () => {
      const elapsed = (Date.now() - startWall) / 1000;
      const remaining = secs - elapsed;
      if (remaining <= 0) {
        setCountdown("Path change expected");
      } else {
        const m = Math.floor(remaining / 60);
        const s = Math.floor(remaining % 60);
        setCountdown(`Path change expected in: ${m > 0 ? `${m}m ${s}s` : `${s}s`}`);
      }
    };
    tick();
    countdownRef.current = setInterval(tick, 1000);
    return () => { if (countdownRef.current) clearInterval(countdownRef.current); };
  }, [tp?.path_valid_seconds, tp?.traced_at]);

  const handleTrace = useCallback(async () => {
    if (!src || !dst || src === dst) return;
    setLoading(true); setError(null);
    try {
      const res = await fetch(`${REST_URL}/api/v1/trace/start`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ src_node: src, dst_node: dst }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        setContinuous(true);
      } else {
        setError(data.error ?? data.detail ?? `Failed (${res.status})`);
      }
    } catch {
      setError("Trace failed");
    } finally {
      setLoading(false);
    }
  }, [src, dst]);

  const handleStop = useCallback(async () => {
    try {
      await fetch(`${REST_URL}/api/v1/trace/stop`, { method: "POST", headers: authHeaders() });
    } catch {}
    setContinuous(false);
    setCountdown(null);
    onTraceResult?.(null);
  }, [onTraceResult]);

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
        {!continuous ? (
          <button className="trace-button" onClick={handleTrace} disabled={loading || !src || !dst || src === dst}>
            {loading ? "Starting..." : "Trace"}
          </button>
        ) : (
          <button className="trace-button" onClick={handleStop} style={{ background: "#4a1010", color: "#ff5555" }}>
            Stop Trace
          </button>
        )}
      </div>

      {error && <div style={{ marginTop: 6, fontSize: 11, color: "#ff5555" }}>{error}</div>}

      {/* Live trace results */}
      {continuous && tp && (
        <div style={{ marginTop: 8 }}>
          {/* Summary line */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span style={{ fontSize: 9, fontWeight: 700, color: "#44cc66", background: "#004400", borderRadius: 2, padding: "1px 5px" }}>LIVE</span>
            <span style={{ fontSize: 11, color: "var(--text-primary)", fontWeight: 600 }}>
              {tp.hops.length} hops
              {tp.rtt_ms != null && ` · ${tp.rtt_ms.toFixed(1)}ms fwd`}
              {tp.reverse_rtt_ms != null && ` / ${tp.reverse_rtt_ms.toFixed(1)}ms rev`}
              {tp.method && ` [${tp.method}]`}
            </span>
          </div>

          {tp.asymmetry_detected && (
            <div style={{ fontSize: 10, color: "#f5a623", marginBottom: 4 }}>Path asymmetry detected</div>
          )}

          {countdown !== null && (
            <div style={{ fontSize: 11, color: "#5ba3d9", fontWeight: 600, marginBottom: 6 }}>{countdown}</div>
          )}

          {/* Side-by-side forward + reverse */}
          <div style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 9, fontWeight: 600, color: "var(--text-dim)", textTransform: "uppercase" as const, letterSpacing: "0.05em", marginBottom: 4 }}>Forward</div>
              <HopList hops={tp.hops} hopRtts={tp.hop_rtts} />
            </div>
            {tp.reverse_hops && tp.reverse_hops.length > 0 && (
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 9, fontWeight: 600, color: "var(--text-dim)", textTransform: "uppercase" as const, letterSpacing: "0.05em", marginBottom: 4 }}>Reverse</div>
                <HopList hops={tp.reverse_hops} hopRtts={tp.reverse_hop_rtts} />
              </div>
            )}
          </div>
        </div>
      )}

      {continuous && !tp && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-dim)" }}>Tracing path...</div>
      )}
      {continuous && tp && tp.hops.length <= 1 && (() => {
        // Check if src/dst have any active links
        const links = snapshot?.links ?? [];
        const srcLinks = links.filter(l => (l.node_a === src || l.node_b === src) && l.state === "active");
        const dstLinks = links.filter(l => (l.node_a === dst || l.node_b === dst) && l.state === "active");
        const issues: string[] = [];
        if (srcLinks.length === 0) issues.push(`${src} has no active links`);
        if (dstLinks.length === 0) issues.push(`${dst} has no active links`);
        return (
          <div style={{ marginTop: 8, fontSize: 11, color: "#f5a623" }}>
            {issues.length > 0
              ? `Waiting for connectivity — ${issues.join(", ")}`
              : "Waiting for route convergence..."}
          </div>
        );
      })()}
    </div>
  );
}

/** Render a hop list with per-hop latency from real cumulative RTT data. */
function HopList({ hops, hopRtts }: { hops: string[]; hopRtts?: (number | null)[] }) {
  return (
    <div style={{
      fontFamily: "JetBrains Mono, monospace", fontSize: 10, lineHeight: 1.8,
      background: "rgba(0,0,0,0.25)", borderRadius: 4, padding: "6px 8px",
    }}>
      {hops.map((hop, i) => {
        const isGS = hop.startsWith("gs-");
        const rtt = hopRtts?.[i] ?? null;
        const prevRtt = i > 0 ? (hopRtts?.[i - 1] ?? null) : null;
        // Per-hop delay = delta between consecutive cumulative RTTs from tracepath
        const delta = rtt != null && prevRtt != null ? rtt - prevRtt : null;

        return (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
            <span style={{ color: "var(--text-dim)", width: 16, textAlign: "right", flexShrink: 0 }}>{i + 1}</span>
            <span style={{ color: isGS ? "#00d4aa" : "#ff8800", flex: 1 }}>{hop}</span>
            {delta != null && delta > 0 && (
              <span style={{ color: "var(--text-secondary)", fontSize: 9, flexShrink: 0 }}>
                {delta.toFixed(1)}ms
              </span>
            )}
            {i === 0 && rtt != null && rtt > 0 && (
              <span style={{ color: "var(--text-secondary)", fontSize: 9, flexShrink: 0 }}>
                {rtt.toFixed(1)}ms
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
