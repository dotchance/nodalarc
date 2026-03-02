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

  // Link breakdown by type
  const intraAreaLinks = activeLinks.filter((l) => l.link_type === "intra_plane_isl");
  const crossAreaLinks = activeLinks.filter((l) => l.link_type === "cross_plane_isl");
  const groundLinks = activeLinks.filter(
    (l) => l.link_type === "ground_uplink" || l.link_type === "ground_downlink"
      || l.node_a.startsWith("gs-") || l.node_b.startsWith("gs-"),
  );

  // Area breakdown: count nodes and links per area
  const areaNodes = new Map<string, number>();
  const areaLinks = new Map<string, number>();
  for (const sat of sats) {
    const area = sat.routing_area ?? "unknown";
    areaNodes.set(area, (areaNodes.get(area) ?? 0) + 1);
  }
  for (const link of activeLinks) {
    // Count link in the area of node_a (for intra-area) or both areas
    const nodeA = snapshot.nodes.find((n) => n.node_id === link.node_a);
    if (nodeA?.routing_area) {
      areaLinks.set(nodeA.routing_area, (areaLinks.get(nodeA.routing_area) ?? 0) + 1);
    }
  }

  return (
    <div>
      <h2>Network Overview</h2>

      {snapshot.routing_stack && (
        <div className="detail-row">
          <span className="detail-label">Routing Stack</span>
          <span className="detail-value">{snapshot.routing_stack}</span>
        </div>
      )}
      {snapshot.constellation_name && (
        <div className="detail-row">
          <span className="detail-label">Constellation</span>
          <span className="detail-value">{snapshot.constellation_name}</span>
        </div>
      )}

      <div className="detail-row">
        <span className="detail-label">Routers</span>
        <span className="detail-value">{sats.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Gateways</span>
        <span className="detail-value">{gss.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Active Links</span>
        <span className="detail-value">{activeLinks.length} / {snapshot.links.length}</span>
      </div>

      <h3>Link Breakdown</h3>
      <div className="detail-row">
        <span className="detail-label">Intra-area</span>
        <span className="detail-value">{intraAreaLinks.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Cross-area</span>
        <span className="detail-value">{crossAreaLinks.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Ground</span>
        <span className="detail-value">{groundLinks.length}</span>
      </div>

      <h3>Routing Areas</h3>
      {[...areaNodes.entries()].sort().map(([area, count]) => (
        <div className="detail-row" key={area}>
          <span className="detail-label">{area}</span>
          <span className="detail-value">
            {count} nodes, {areaLinks.get(area) ?? 0} links
          </span>
        </div>
      ))}

      {snapshot.active_flows.length > 0 && (
        <>
          <h3>Flow Health</h3>
          {snapshot.active_flows.map((f) => {
            const trace = snapshot.traced_paths.find((t) => t.flow_id === f.flow_id);
            return (
              <div className="detail-row" key={f.flow_id}>
                <span className="detail-label">{f.src_node} → {f.dst_node}</span>
                <span className="detail-value">
                  {trace ? `${trace.hops.length} hops` : "no trace"} — {f.protocol}
                </span>
              </div>
            );
          })}
        </>
      )}

      <h3>Trace Path</h3>
      <TraceDialog groundStations={gss} />
    </div>
  );
}
