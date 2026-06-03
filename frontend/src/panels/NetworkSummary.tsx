// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Network summary — shown when nothing is selected. */

import type { StateSnapshot, Selection } from "../types";
import { isGroundLinkState } from "../networkIdentity";

interface NetworkSummaryProps {
  snapshot: StateSnapshot;
  onSelect: (sel: Selection | null) => void;
}

export function NetworkSummary({ snapshot, onSelect: _onSelect }: NetworkSummaryProps) {
  const sats = snapshot.nodes.filter((n) => n.node_type === "satellite");
  const gss = snapshot.nodes.filter((n) => n.node_type === "ground_station");
  const activeLinks = snapshot.links.filter((l) => l.state === "active");

  // Link breakdown by authoritative link_type.
  let intraCount = 0;
  let crossCount = 0;
  let groundCount = 0;
  for (const l of activeLinks) {
    if (isGroundLinkState(l)) groundCount++;
    else if (l.link_type === "intra_plane_isl") intraCount++;
    else crossCount++;
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

    </div>
  );
}
