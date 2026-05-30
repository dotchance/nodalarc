// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Satellite detail panel — role, area, adjacencies, position. */

import { useEffect, useState } from "react";
import { translateReason } from "../translate";
import { areaCSSColor } from "../globe/colors";
import type { NodeState, StateSnapshot, Selection } from "../types";
import { fetchGroundDecisions, type GroundDecisionsSnapshot } from "../explain/client";
import { candidateStatus } from "../explain/derive";
import { CandidateRow } from "../explain/components/CandidateRow";
import { PairInspectorView } from "../explain/components/PairInspectorView";

interface SatelliteDetailProps {
  node: NodeState;
  snapshot: StateSnapshot;
  onSelect: (sel: Selection | null) => void;
}

const _ordered = (a: string, b: string): string => [a, b].sort().join("|");

function linkTypeLabel(linkType: string | null): string {
  switch (linkType) {
    case "intra_plane_isl": return "intra-area";
    case "cross_plane_isl": return "cross-area";
    case "ground_uplink": return "ground";
    case "ground_downlink": return "ground";
    default: return linkType ?? "unknown";
  }
}

export function SatelliteDetail({ node, snapshot, onSelect }: SatelliteDetailProps) {
  const [inspectedGs, setInspectedGs] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<GroundDecisionsSnapshot | null>(null);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    // Clear the previous satellite's slice immediately so a stale cross-node candidate
    // never renders for a moment after switching satellites.
    setDecisions(null);
    const load = async () => {
      try {
        const snap = await fetchGroundDecisions(node.node_id, controller.signal);
        if (alive) setDecisions(snap);
      } catch {
        // candidate list is non-essential; keep prior state on transient errors
      }
    };
    void load();
    const timer = window.setInterval(load, 2000);
    return () => {
      alive = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [node.node_id]);

  useEffect(() => {
    setInspectedGs(null);
  }, [node.node_id]);

  // Find connected links
  const connectedLinks = snapshot.links.filter(
    (l) => l.node_a === node.node_id || l.node_b === node.node_id,
  );
  const islLinks = connectedLinks.filter(
    (l) => !l.node_a.startsWith("gs-") && !l.node_b.startsWith("gs-"),
  );
  const gndLinks = connectedLinks.filter(
    (l) => l.node_a.startsWith("gs-") || l.node_b.startsWith("gs-"),
  );

  // Determine role: Router vs Router (ABR)
  const linkedAreas = new Set<string>();
  for (const l of connectedLinks) {
    const peerNode = snapshot.nodes.find(
      (n) => n.node_id === (l.node_a === node.node_id ? l.node_b : l.node_a),
    );
    if (peerNode?.routing_area) linkedAreas.add(peerNode.routing_area);
  }
  if (node.routing_area) linkedAreas.add(node.routing_area);
  const role = linkedAreas.size > 1 ? "Router (ABR)" : "Router";

  const selectPeer = (peerId: string) => {
    const type = peerId.startsWith("gs-") ? "ground_station" : "satellite";
    onSelect({ type, id: peerId });
  };

  const selectLink = (nodeA: string, nodeB: string) => {
    const key = nodeA < nodeB ? `${nodeA}:${nodeB}` : `${nodeB}:${nodeA}`;
    onSelect({ type: "link", id: key });
  };

  if (inspectedGs) {
    return (
      <PairInspectorView
        gsId={inspectedGs}
        satId={node.node_id}
        onBack={() => setInspectedGs(null)}
      />
    );
  }

  const withheld = new Set(
    (decisions?.unscheduled_pairs ?? []).map((u) => _ordered(u.pair[0], u.pair[1])),
  );
  const unschedReason = new Map(
    (decisions?.unscheduled_pairs ?? []).map((u) => [
      _ordered(u.pair[0], u.pair[1]),
      u.unscheduled_reason,
    ]),
  );
  // Candidate state comes from the OME decision + reason registry, NOT snapshot.links
  // (OME's forwarding/authority view). A scheduled pair reads neutral here; its precise
  // connected/faulted state comes from the kernel-actual source in the inspector — the
  // GS card already moved off authority-as-connected and the sat panel must too.
  // The server already slices to this satellite's pairs (?node=), so every decision
  // involves it.
  const candidateGs = (decisions?.decisions ?? [])
    .map((d) => {
      const gs = d.pair[0] === node.node_id ? d.pair[1] : d.pair[0];
      const key = _ordered(d.pair[0], d.pair[1]);
      const status = candidateStatus({
        visible: d.visible,
        isWithheld: withheld.has(key),
        rejectReason: d.reject_reason,
        unscheduledReason: unschedReason.get(key) ?? null,
      });
      return { gs, d, status };
    });

  return (
    <div>
      <h2>{node.node_id}</h2>
      <div className="detail-row">
        <span className="detail-label">Role</span>
        <span className="detail-value">{role}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Routing Area</span>
        <span className="detail-value" style={{ color: areaCSSColor(node.routing_area) }}>
          {node.routing_area ?? "none"}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Plane / Slot</span>
        <span className="detail-value">P{node.plane ?? "?"} / S{node.slot ?? "?"}</span>
      </div>

      <h3>Adjacencies ({node.neighbor_count})</h3>
      <div className="detail-row">
        <span className="detail-label">ISL links</span>
        <span className="detail-value">{node.isl_count}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Ground links</span>
        <span className="detail-value">{node.gnd_count}</span>
      </div>

      {islLinks.length > 0 && (
        <>
          <h3>ISL Links</h3>
          {islLinks.map((l) => {
            const peer = l.node_a === node.node_id ? l.node_b : l.node_a;
            const localIface = l.node_a === node.node_id ? l.interface_a : l.interface_b;
            const stateUp = l.state === "active";
            return (
              <div className="detail-row detail-row--clickable" key={`${l.node_a}:${l.node_b}`}>
                <span
                  className="detail-label detail-label--link"
                  onClick={() => selectPeer(peer)}
                  title={`Select ${peer}`}
                >
                  {localIface ? `${localIface}: ` : ""}{peer}
                </span>
                <span
                  className="detail-value"
                  onClick={() => selectLink(l.node_a, l.node_b)}
                  title="Select link"
                >
                  <span className={stateUp ? "link-state--up" : "link-state--down"}>
                    {stateUp ? "UP" : "DOWN"}
                  </span>
                  {" "}{l.latency_ms.toFixed(1)}ms {linkTypeLabel(l.link_type)}
                  {!stateUp && l.link_reason ? ` — ${translateReason(l.link_reason)}` : ""}
                </span>
              </div>
            );
          })}
        </>
      )}

      {gndLinks.length > 0 && (
        <>
          <h3>Ground Links</h3>
          {gndLinks.map((l) => {
            const peer = l.node_a === node.node_id ? l.node_b : l.node_a;
            const localIface = l.node_a === node.node_id ? l.interface_a : l.interface_b;
            return (
              <div className="detail-row detail-row--clickable" key={`${l.node_a}:${l.node_b}`}>
                <span
                  className="detail-label detail-label--link"
                  onClick={() => selectPeer(peer)}
                  title={`Select ${peer}`}
                >
                  {localIface ? `${localIface}: ` : ""}{peer}
                </span>
                <span
                  className="detail-value"
                  onClick={() => selectLink(l.node_a, l.node_b)}
                  title="Select link"
                >
                  {l.latency_ms.toFixed(1)}ms — {translateReason(l.link_reason)}
                </span>
              </div>
            );
          })}
        </>
      )}

      {candidateGs.length > 0 ? (
        <>
          <h3>Candidate Ground Stations ({candidateGs.length})</h3>
          {candidateGs.map(({ gs, d, status }) => (
            <CandidateRow
              key={gs}
              node={gs}
              family={status.family}
              label={status.label}
              detail={d.elevation_deg != null ? `${Math.round(d.elevation_deg)} deg el` : null}
              onClick={() => setInspectedGs(gs)}
            />
          ))}
        </>
      ) : null}

      <h3>Position</h3>
      <div className="detail-row">
        <span className="detail-label">Lat / Lon</span>
        <span className="detail-value">
          {node.lat_deg.toFixed(2)}&deg; / {node.lon_deg.toFixed(2)}&deg;
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Altitude</span>
        <span className="detail-value">{node.alt_km.toFixed(1)} km</span>
      </div>
    </div>
  );
}
