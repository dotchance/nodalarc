/** Trace path dialog — two GS dropdowns + trace button. */

import { useState } from "react";
import { REST_URL } from "../config";
import type { NodeState } from "../types";

interface TraceDialogProps {
  groundStations: NodeState[];
}

export function TraceDialog({ groundStations }: TraceDialogProps) {
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const [result, setResult] = useState<string | null>(null);

  const handleTrace = async () => {
    if (!src || !dst) return;
    try {
      const res = await fetch(`${REST_URL}/api/v1/trace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src_node: src, dst_node: dst }),
      });
      const data = await res.json();
      if (data.hops && data.hops.length > 0) {
        setResult(`Path: ${(data.hops as string[]).join(" → ")}`);
      } else {
        setResult(data.note ?? "No path found");
      }
    } catch {
      setResult("Trace failed");
    }
  };

  return (
    <div className="trace-dialog">
      <select value={src} onChange={(e) => setSrc(e.target.value)}>
        <option value="">Source GS...</option>
        {groundStations.map((gs) => (
          <option key={gs.node_id} value={gs.node_id}>
            {gs.node_id}
          </option>
        ))}
      </select>
      <select value={dst} onChange={(e) => setDst(e.target.value)}>
        <option value="">Dest GS...</option>
        {groundStations.map((gs) => (
          <option key={gs.node_id} value={gs.node_id}>
            {gs.node_id}
          </option>
        ))}
      </select>
      <button className="trace-button" onClick={handleTrace}>
        Trace Path
      </button>
      {result && (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", padding: "4px 0" }}>
          {result}
        </div>
      )}
    </div>
  );
}
