/** Trace path dialog — source/dest dropdowns + trace button.
 *  Queries live forwarding tables on running containers via VS-API,
 *  displays detailed hop-by-hop result, and passes TracedPath to
 *  parent for globe/topology visualization.
 */

import { useState, useEffect } from "react";
import { REST_URL, authHeaders } from "../config";
import type { NodeState, TracedPath } from "../types";

interface TraceDialogProps {
  nodes: NodeState[];
  selectedNodeId?: string | null;
  onTraceResult?: (path: TracedPath | null) => void;
}

interface HopDetail {
  node_id: string;
  action: string | null;
  in_label: number | null;
  out_label: number | null;
  out_interface: string | null;
  latency_to_next_ms: number | null;
}

interface TraceResult {
  hops: string[];
  hopDetails: HopDetail[];
  summary: string;
  method: string;
  error?: string;
}

export function TraceDialog({ nodes, selectedNodeId, onTraceResult }: TraceDialogProps) {
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const [result, setResult] = useState<TraceResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (selectedNodeId) setSrc(selectedNodeId);
  }, [selectedNodeId]);

  const sorted = [...nodes].sort((a, b) => {
    if (a.node_type !== b.node_type) return a.node_type === "ground_station" ? -1 : 1;
    return a.node_id.localeCompare(b.node_id);
  });

  const handleTrace = async () => {
    if (!src || !dst || src === dst) return;
    setLoading(true);
    try {
      const res = await fetch(`${REST_URL}/api/v1/trace`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ src_node: src, dst_node: dst }),
      });
      const data = await res.json();
      if (data.hops && data.hops.length > 0) {
        const hops = data.hops as string[];
        const method = data.method ?? "trace";
        const latency = data.total_latency_ms
          ? `${(data.total_latency_ms as number).toFixed(1)}ms`
          : "";
        const hopDetails: HopDetail[] = data.hop_details ?? [];
        setResult({
          hops,
          hopDetails,
          summary: `${hops.length} hops, ${latency} [${method}]`,
          method,
        });
        onTraceResult?.({ flow_id: "__user_trace__", src_node: src, dst_node: dst, hops });
      } else {
        setResult({
          hops: [],
          hopDetails: [],
          summary: "",
          method: "",
          error: data.note ?? data.error ?? "No path found",
        });
        onTraceResult?.(null);
      }
    } catch {
      setResult({ hops: [], hopDetails: [], summary: "", method: "", error: "Trace failed" });
      onTraceResult?.(null);
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setResult(null);
    onTraceResult?.(null);
  };

  function formatAction(action: string | null): string {
    if (!action) return "";
    return action.toUpperCase();
  }

  function formatLabels(hop: HopDetail): string {
    if (!hop.action) return "";
    const a = formatAction(hop.action);
    if (a === "PUSH") return `${hop.out_label ?? ""}`;
    if (a === "POP") return `${hop.in_label ?? ""}`;
    if (a === "SWAP") return `${hop.in_label ?? ""} \u2192 ${hop.out_label ?? ""}`;
    return "";
  }

  return (
    <div className="trace-dialog">
      <h3>Trace Path</h3>
      <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
        <select value={src} onChange={(e) => setSrc(e.target.value)} style={{ flex: 1 }}>
          <option value="">Source...</option>
          {sorted.map((n) => (
            <option key={n.node_id} value={n.node_id}>{n.node_id}</option>
          ))}
        </select>
        <select value={dst} onChange={(e) => setDst(e.target.value)} style={{ flex: 1 }}>
          <option value="">Destination...</option>
          {sorted.map((n) => (
            <option key={n.node_id} value={n.node_id}>{n.node_id}</option>
          ))}
        </select>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button className="trace-button" onClick={handleTrace} disabled={loading || !src || !dst || src === dst}>
          {loading ? "Tracing..." : "Trace"}
        </button>
        {result && (
          <button className="trace-button" onClick={handleClear}>Clear</button>
        )}
      </div>

      {result && !result.error && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, color: "var(--text-primary)", fontWeight: 600, marginBottom: 6 }}>
            {result.summary}
          </div>
          <div style={{
            fontFamily: "JetBrains Mono, monospace",
            fontSize: 10,
            lineHeight: 1.8,
            background: "rgba(0,0,0,0.25)",
            borderRadius: 4,
            padding: "6px 8px",
          }}>
            {result.hopDetails.length > 0 ? (
              result.hopDetails.map((hop, i) => {
                const isGS = hop.node_id.startsWith("gs-");
                const nodeColor = isGS ? "#00d4aa" : "#ff8800";
                const action = formatAction(hop.action);
                const labels = formatLabels(hop);
                const iface = hop.out_interface ?? "";
                const lat = hop.latency_to_next_ms != null && hop.latency_to_next_ms > 0
                  ? `${hop.latency_to_next_ms.toFixed(1)}ms`
                  : "";

                return (
                  <div key={i} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                    <span style={{ color: "var(--text-dim)", width: 16, textAlign: "right", flexShrink: 0 }}>{i + 1}</span>
                    <span style={{ color: nodeColor, width: 100, flexShrink: 0 }}>{hop.node_id}</span>
                    {action && (
                      <span style={{ color: action === "POP" ? "#ff5555" : action === "PUSH" ? "#55aaff" : "#cccc55", width: 36, flexShrink: 0 }}>{action}</span>
                    )}
                    {labels && (
                      <span style={{ color: "var(--text-secondary)", width: 80, flexShrink: 0 }}>{labels}</span>
                    )}
                    {iface && (
                      <span style={{ color: "var(--text-dim)", width: 36, flexShrink: 0 }}>{iface}</span>
                    )}
                    {lat && (
                      <span style={{ color: "var(--text-dim)", textAlign: "right", marginLeft: "auto" }}>{lat}</span>
                    )}
                  </div>
                );
              })
            ) : (
              /* Fallback: just show node IDs if no hop_details */
              result.hops.map((hop, i) => (
                <div key={i} style={{ display: "flex", gap: 6 }}>
                  <span style={{ color: "var(--text-dim)", width: 16, textAlign: "right" }}>{i + 1}</span>
                  <span style={{ color: hop.startsWith("gs-") ? "#00d4aa" : "#ff8800" }}>{hop}</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {result?.error && (
        <div style={{ marginTop: 8, fontSize: 11, color: "#ff5555" }}>{result.error}</div>
      )}
    </div>
  );
}
