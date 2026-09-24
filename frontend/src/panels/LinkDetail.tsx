// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Link detail panel — state, metrics, flow paths, history. */

import { useEffect, useState } from "react";
import { translateLinkType } from "../translate";
import { linkEventLabel } from "../explain/linkEvents";
import { REST_URL, authHeaders } from "../config";
import { apiErrorFromException, apiErrorMessage } from "../ui/apiError";
import type { LinkHistoryPage, LinkState, StateSnapshot } from "../types";
import { instanceLabel, interfaceInstances } from "../routing/instances";

interface LinkDetailProps {
  link: LinkState;
  snapshot: StateSnapshot;
}

/** Recorded events the panel shows: the newest of the selected link. */
const HISTORY_SHOWN = 20;

/** The routing instances one end runs on its interface of the link, with the
 *  interface's OSPF area. */
function LinkEndRouting({
  snapshot,
  nodeId,
  interfaceName,
}: {
  snapshot: StateSnapshot;
  nodeId: string;
  interfaceName: string;
}) {
  const node = snapshot.nodes.find((candidate) => candidate.node_id === nodeId);
  if (!node || interfaceName === "") return null;
  const entries = interfaceInstances(node, interfaceName);
  const text =
    entries.length === 0
      ? "no routing instance"
      : entries
          .map(({ instance, areaId }) => {
            const label = instanceLabel({ domainId: instance.domain_id, protocol: instance.protocol });
            return areaId === null ? label : `${label} area ${areaId}`;
          })
          .join("; ");
  return (
    <div className="detail-row">
      <span className="detail-label">
        {nodeId} {interfaceName}
      </span>
      <span className="detail-value">{text}</span>
    </div>
  );
}

/** A terminal rate in Gb/s from 1000 Mb/s up and in Mb/s below, keeping one
 *  decimal where the declared rate has one (1.2 Gb/s, 23.6 Mb/s). */
export function formatRate(mbps: number): string {
  const [value, unit] = mbps >= 1000 ? [mbps / 1000, "Gb/s"] : [mbps, "Mb/s"];
  const shown = Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
  return `${shown} ${unit}`;
}

/** Which end holds a direction back: the lower of the sender's transmit and
 *  the receiver's receive. Equal rates hold neither back. */
function directionLimit(senderTransmit: number, receiverReceive: number): "sender" | "receiver" | null {
  if (senderTransmit < receiverReceive) return "sender";
  if (receiverReceive < senderTransmit) return "receiver";
  return null;
}

function RateValue({
  label,
  mbps,
  limiting,
  end,
}: {
  label: "TX" | "RX";
  mbps: number;
  limiting: boolean;
  end?: boolean;
}) {
  const classes = ["link-rate"];
  if (end) classes.push("link-rate--end");
  if (limiting) classes.push("link-rate--limiting");
  return (
    <span className={classes.join(" ")} data-limiting={limiting ? "true" : undefined}>
      <span className="link-rate-label">{label}</span>
      {formatRate(mbps)}
    </span>
  );
}

/** The link's two directions, each drawn from its sender's transmit rate to
 *  its receiver's receive rate. The value that holds a direction back is
 *  highlighted. */
function LinkRates({ link }: { link: LinkState }) {
  const aToB = directionLimit(link.transmit_mbps_a, link.receive_mbps_b);
  const bToA = directionLimit(link.transmit_mbps_b, link.receive_mbps_a);
  return (
    <div className="link-rates" role="group" aria-label="Terminal rates">
      <span className="link-rates-node" title={link.node_a}>{link.node_a}</span>
      <span />
      <span className="link-rates-node link-rates-node--end" title={link.node_b}>{link.node_b}</span>

      <RateValue label="TX" mbps={link.transmit_mbps_a} limiting={aToB === "sender"} />
      <span className="link-rates-arrow link-rates-arrow--to-b" aria-hidden="true" />
      <RateValue label="RX" mbps={link.receive_mbps_b} limiting={aToB === "receiver"} end />

      <RateValue label="RX" mbps={link.receive_mbps_a} limiting={bToA === "receiver"} />
      <span className="link-rates-arrow link-rates-arrow--to-a" aria-hidden="true" />
      <RateValue label="TX" mbps={link.transmit_mbps_b} limiting={bToA === "sender"} end />
    </div>
  );
}

export function LinkDetail({ link, snapshot }: LinkDetailProps) {
  const [history, setHistory] = useState<LinkHistoryPage | null>(null);
  // Why no history is shown: recording is off for the session, recording
  // failed, or the request failed. Stated, never shown as an empty history.
  const [historyUnavailable, setHistoryUnavailable] = useState<string | null>(null);

  // Fetch the newest recorded events of this link on select.
  useEffect(() => {
    let current = true;
    const fetchHistory = async () => {
      setHistory(null);
      setHistoryUnavailable(null);
      const query = new URLSearchParams({
        node: link.node_a,
        peer: link.node_b,
        order: "newest_first",
        limit: String(HISTORY_SHOWN),
      });
      try {
        const res = await fetch(`${REST_URL}/api/v1/links?${query}`, { headers: authHeaders() });
        if (!res.ok) {
          const message = await apiErrorMessage(res);
          if (current) setHistoryUnavailable(message);
          return;
        }
        const page = (await res.json()) as LinkHistoryPage;
        if (current) setHistory(page);
      } catch (err) {
        if (current) setHistoryUnavailable(apiErrorFromException(err));
      }
    };
    fetchHistory();
    return () => {
      current = false;
    };
  }, [link.node_a, link.node_b]);

  // Find flows traversing this link
  const flowsOnLink = snapshot.traced_paths.filter((tp) => {
    for (let i = 0; i < tp.hops.length - 1; i++) {
      const a = tp.hops[i]!;
      const b = tp.hops[i + 1]!;
      if (
        (a === link.node_a && b === link.node_b) ||
        (a === link.node_b && b === link.node_a)
      ) {
        return true;
      }
    }
    return false;
  });

  return (
    <div>
      <h2>Link: {link.node_a} ↔ {link.node_b}</h2>
      <div className="detail-row">
        <span className="detail-label">State</span>
        <span className={`detail-value detail-value--${link.state === "active" ? "active" : "failed"}`}>
          {link.state}
        </span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Reason</span>
        <span className="detail-value">{linkEventLabel(link.link_reason)}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Type</span>
        <span className="detail-value">{translateLinkType(link.link_type)}</span>
      </div>
      {link.link_rule_id && (
        <div className="detail-row">
          <span className="detail-label">Rule</span>
          <span className="detail-value">{link.link_rule_id}</span>
        </div>
      )}
      {link.topology_mode && (
        <div className="detail-row">
          <span className="detail-label">Topology</span>
          <span className="detail-value">{link.topology_mode}</span>
        </div>
      )}
      {link.endpoint_segments && (
        <div className="detail-row">
          <span className="detail-label">Segments</span>
          <span className="detail-value">{link.endpoint_segments.join(" ↔ ")}</span>
        </div>
      )}
      <LinkEndRouting snapshot={snapshot} nodeId={link.node_a} interfaceName={link.interface_a} />
      <LinkEndRouting snapshot={snapshot} nodeId={link.node_b} interfaceName={link.interface_b} />

      <h3>Metrics</h3>
      <div className="detail-row">
        <span className="detail-label">Latency</span>
        <span className="detail-value">{link.latency_ms.toFixed(1)} ms</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Range</span>
        <span className="detail-value">{link.range_km.toFixed(0)} km</span>
      </div>
      <LinkRates link={link} />
      {link.traffic_load_pct != null && (
        <div className="detail-row">
          <span className="detail-label">Load</span>
          <span className="detail-value">{link.traffic_load_pct.toFixed(1)}%</span>
        </div>
      )}

      {flowsOnLink.length > 0 && (
        <>
          <h3>Flow Paths</h3>
          {flowsOnLink.map((tp) => (
            <div className="detail-row" key={tp.flow_id}>
              <span className="detail-label">{tp.flow_id}</span>
              <span className="detail-value">{tp.hops.length} hops</span>
            </div>
          ))}
        </>
      )}

      {historyUnavailable !== null && (
        <>
          <h3>History</h3>
          <div className="detail-row">
            <span className="detail-value">{historyUnavailable}</span>
          </div>
        </>
      )}
      {history !== null && history.returned > 0 && (
        <>
          <h3>History (last {history.returned} of {history.total})</h3>
          {history.retained_from !== null && (
            <div className="detail-row">
              <span className="detail-value" style={{ fontSize: 10 }}>
                Older events were dropped to keep history within its size budget
              </span>
            </div>
          )}
          {[...history.events].reverse().map((h, i) => (
            <div className="detail-row" key={i}>
              <span className="detail-label" style={{ fontSize: 10 }}>
                {h.sim_time?.substring(11, 19) ?? ""}
              </span>
              <span className="detail-value" style={{ fontSize: 10 }}>
                {h.event_type} — {linkEventLabel(h.reason)}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
