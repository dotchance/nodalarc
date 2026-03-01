/** Network summary — shown when nothing is selected. */

import { TraceDialog } from "./TraceDialog";
import type { StateSnapshot, Selection } from "../types";

interface NetworkSummaryProps {
  snapshot: StateSnapshot;
  onSelect: (sel: Selection | null) => void;
}

export function NetworkSummary({ snapshot, onSelect: _onSelect }: NetworkSummaryProps) {
  const sats = snapshot.nodes.filter((n) => n.node_type === "satellite");
  const gss = snapshot.nodes.filter((n) => n.node_type === "ground_station");
  const activeLinks = snapshot.links.filter((l) => l.state === "active");

  // Area breakdown
  const areas = new Map<string, number>();
  for (const sat of sats) {
    const area = sat.routing_area ?? "unknown";
    areas.set(area, (areas.get(area) ?? 0) + 1);
  }

  return (
    <div>
      <h2>Network Overview</h2>

      <div className="detail-row">
        <span className="detail-label">Satellites</span>
        <span className="detail-value">{sats.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Ground Stations</span>
        <span className="detail-value">{gss.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Active Links</span>
        <span className="detail-value">{activeLinks.length} / {snapshot.links.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Active Flows</span>
        <span className="detail-value">{snapshot.active_flows.length}</span>
      </div>

      <h3>Routing Areas</h3>
      {[...areas.entries()].sort().map(([area, count]) => (
        <div className="detail-row" key={area}>
          <span className="detail-label">{area}</span>
          <span className="detail-value">{count} sats</span>
        </div>
      ))}

      {snapshot.active_flows.length > 0 && (
        <>
          <h3>Flows</h3>
          {snapshot.active_flows.map((f) => (
            <div className="detail-row" key={f.flow_id}>
              <span className="detail-label">{f.src_node} → {f.dst_node}</span>
              <span className="detail-value">{f.protocol}</span>
            </div>
          ))}
        </>
      )}

      <h3>Trace Path</h3>
      <TraceDialog groundStations={gss} />
    </div>
  );
}
