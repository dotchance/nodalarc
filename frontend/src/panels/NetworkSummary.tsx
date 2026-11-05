// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Network summary — shown when nothing is selected. */

import { instanceLabel } from "../routing/instances";
import { formatDuration, translateLinkType } from "../translate";
import type {
  LinkEndCloseCause,
  LinkEndLoss,
  PublicLinkType,
  StateSnapshot,
} from "../generated/vsApi";
import type { NodeRole } from "../builder/generated/builderApi";

const ROLES: readonly NodeRole[] = ["router", "host", "forwarding_only"];
const LINK_KINDS: readonly PublicLinkType[] = ["isl", "inter_constellation", "inter_body_relay", "ground"];
const ROLE_COUNT_LABELS: Record<NodeRole, string> = {
  router: "Routers",
  host: "Hosts",
  forwarding_only: "Forwarding only",
};

// The ended contacts the overview lists, newest first.
const CONTACT_LOSSES_SHOWN = 10;

const CLOSE_CAUSE_LABELS: Record<LinkEndCloseCause, string> = {
  end_of_contact: "contact ended",
  drain_deadline: "drain deadline",
  seek: "seek",
  scenario: "scenario command",
  reopened: "reopened while draining",
};

/** One end's losses: what its queue still held at the close (a count when both transmitters
 *  had stopped, a sample on a local link), what its queue dropped at its limit, and what its
 *  stopped transmitter refused. */
function endLossText(end: LinkEndLoss): string {
  const held = `${end.discarded_at_close} discarded (${end.discarded_exact ? "count" : "sample"})`;
  return `${CLOSE_CAUSE_LABELS[end.cause]}: ${held}, ${end.queue_dropped} dropped at the queue limit, ${end.admission_taken} refused after the stop`;
}

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

  // Link breakdown by the kind VS-API reports.
  const kindCounts = new Map<PublicLinkType, number>(LINK_KINDS.map((kind) => [kind, 0]));
  for (const l of activeLinks) kindCounts.set(l.link_type, (kindCounts.get(l.link_type) ?? 0) + 1);

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
      {LINK_KINDS.map((kind) => (
        <div className="detail-row" key={kind}>
          <span className="detail-label">{translateLinkType(kind)}</span>
          <span className="detail-value">{kindCounts.get(kind)}</span>
        </div>
      ))}

      {snapshot.draining_directions.length > 0 && (
        <>
          <h3>Draining</h3>
          {snapshot.draining_directions.map((direction) => {
            const remainingMs =
              Date.parse(direction.last_arrival_at) - Date.parse(snapshot.wall_time);
            return (
              <div
                className="detail-row"
                key={`${direction.contact_id}:${direction.transmitter_node_id}->${direction.receiver_node_id}`}
                title={
                  "The contact ended and the transmitter stopped sending. Packets it sent " +
                  "before then are still in flight and arrive until the last arrival, in " +
                  "wall time at every playback speed."
                }
              >
                <span className="detail-label">
                  {direction.transmitter_node_id} → {direction.receiver_node_id}
                </span>
                <span className="detail-value">
                  {remainingMs > 0
                    ? `last arrival in ${formatDuration(remainingMs)}`
                    : "last arrival due"}
                </span>
              </div>
            );
          })}
        </>
      )}

      {snapshot.contact_losses.length > 0 && (
        <>
          <h3>Contact Losses</h3>
          {[...snapshot.contact_losses]
            .reverse()
            .slice(0, CONTACT_LOSSES_SHOWN)
            .map((contact) => (
              <div key={`${contact.node_a}|${contact.node_b}|${contact.wall_time}`}>
                <div className="detail-row">
                  <span className="detail-label">
                    {contact.node_a} ↔ {contact.node_b}
                  </span>
                  <span className="detail-value">{contact.reason}</span>
                </div>
                {contact.ends.map((end) => (
                  <div className="detail-row" key={`${end.node_id}/${end.interface}`}>
                    <span className="detail-label">
                      {end.node_id}/{end.interface}
                    </span>
                    <span className="detail-value">{endLossText(end)}</span>
                  </div>
                ))}
              </div>
            ))}
        </>
      )}

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
