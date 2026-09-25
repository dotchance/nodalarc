// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Network summary — shown when nothing is selected. */

import type { NodeRole, StateSnapshot } from "../types";
import { isGroundLinkState } from "../networkIdentity";
import { instanceLabel } from "../routing/instances";

const ROLES: readonly NodeRole[] = ["router", "host", "forwarding_only"];
const ROLE_COUNT_LABELS: Record<NodeRole, string> = {
  router: "Routers",
  host: "Hosts",
  forwarding_only: "Forwarding only",
};

interface InstanceTally {
  label: string;
  participants: number;
  /** Participants per area, for IS-IS and OSPF instances. */
  areas: Map<string, number>;
}

interface NetworkSummaryProps {
  snapshot: StateSnapshot;
}

export function NetworkSummary({ snapshot }: NetworkSummaryProps) {
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

  // Roles and routing instances, read from the backend's facts.
  const roleCounts = new Map<NodeRole, number>(ROLES.map((role) => [role, 0]));
  let areaBorderRouters = 0;
  let asBoundaryRouters = 0;
  const instances = new Map<string, InstanceTally>();
  for (const node of snapshot.nodes) {
    roleCounts.set(node.role, (roleCounts.get(node.role) ?? 0) + 1);
    if (node.routing_instances.some((instance) => instance.area_border)) areaBorderRouters++;
    if (node.routing_instances.some((instance) => instance.as_boundary)) asBoundaryRouters++;
    for (const instance of node.routing_instances) {
      let tally = instances.get(instance.domain_id);
      if (!tally) {
        tally = {
          label: instanceLabel({ domainId: instance.domain_id, protocol: instance.protocol }),
          participants: 0,
          areas: new Map(),
        };
        instances.set(instance.domain_id, tally);
      }
      tally.participants++;
      for (const area of instance.areas) tally.areas.set(area, (tally.areas.get(area) ?? 0) + 1);
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

      <h3>Nodes</h3>
      <div className="detail-row">
        <span className="detail-label">Satellites</span>
        <span className="detail-value">{sats.length}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Ground Stations</span>
        <span className="detail-value">{gss.length}</span>
      </div>
      {ROLES.map((role) => (
        <div className="detail-row" key={role}>
          <span className="detail-label">{ROLE_COUNT_LABELS[role]}</span>
          <span className="detail-value">{roleCounts.get(role) ?? 0}</span>
        </div>
      ))}
      <div className="detail-row">
        <span className="detail-label">Area Border Routers</span>
        <span className="detail-value">{areaBorderRouters}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">AS Boundary Routers</span>
        <span className="detail-value">{asBoundaryRouters}</span>
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

      <h3>Routing Instances</h3>
      {[...instances.entries()].map(([domainId, tally]) => (
        <div key={domainId}>
          <div className="detail-row">
            <span className="detail-label">{tally.label}</span>
            <span className="detail-value">{tally.participants} participants</span>
          </div>
          {[...tally.areas.entries()].sort().map(([area, count]) => (
            <div className="detail-row" key={area}>
              <span className="detail-label">Area {area}</span>
              <span className="detail-value">{count} nodes</span>
            </div>
          ))}
        </div>
      ))}


    </div>
  );
}
