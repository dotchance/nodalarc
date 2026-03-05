/** Network summary — shown when nothing is selected. */

import { TraceDialog } from "./TraceDialog";
import type { StateSnapshot, Selection } from "../types";

interface NetworkSummaryProps {
  snapshot: StateSnapshot;
  onSelect: (sel: Selection | null) => void;
}

/** Classify a link by its endpoint node IDs. */
function classifyLink(nodeA: string, nodeB: string): "ground" | "intra_plane" | "cross_plane" {
  if (nodeA.startsWith("gs-") || nodeB.startsWith("gs-")) return "ground";
  // Parse plane from sat-P00S05 format
  const planeA = nodeA.match(/sat-P(\d+)/)?.[1];
  const planeB = nodeB.match(/sat-P(\d+)/)?.[1];
  if (planeA != null && planeB != null && planeA === planeB) return "intra_plane";
  return "cross_plane";
}

export function NetworkSummary({ snapshot, onSelect: _onSelect }: NetworkSummaryProps) {
  const sats = snapshot.nodes.filter((n) => n.node_type === "satellite");
  const gss = snapshot.nodes.filter((n) => n.node_type === "ground_station");
  const activeLinks = snapshot.links.filter((l) => l.state === "active");

  // Link breakdown by type (derived from node IDs)
  let intraCount = 0;
  let crossCount = 0;
  let groundCount = 0;
  for (const l of activeLinks) {
    const cls = classifyLink(l.node_a, l.node_b);
    if (cls === "intra_plane") intraCount++;
    else if (cls === "cross_plane") crossCount++;
    else groundCount++;
  }

  // Area breakdown: count nodes and links per area
  const areaNodes = new Map<string, number>();
  const areaLinks = new Map<string, number>();
  for (const sat of sats) {
    const area = sat.routing_area ?? "unknown";
    areaNodes.set(area, (areaNodes.get(area) ?? 0) + 1);
  }
  for (const link of activeLinks) {
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
        <span className="detail-label">Ground Stations</span>
        <span className="detail-value">{gss.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Active Links</span>
        <span className="detail-value">{activeLinks.length}</span>
      </div>

      <h3>Link Breakdown</h3>
      <div className="detail-row">
        <span className="detail-label">Intra-plane ISL</span>
        <span className="detail-value">{intraCount}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Cross-plane ISL</span>
        <span className="detail-value">{crossCount}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Ground</span>
        <span className="detail-value">{groundCount}</span>
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
