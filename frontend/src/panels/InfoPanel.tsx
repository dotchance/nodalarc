// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Right panel — selection detail on top, the trace-path tool pinned below.
 *  The old embedded events list is gone (owner direction: inaccurate
 *  precursor to the logs); the network event feed lives in the System Logs
 *  window's Events mode. */

import { NetworkSummary } from "./NetworkSummary";
import { SatelliteDetail } from "./SatelliteDetail";
import { GroundStationDetail } from "./GroundStationDetail";
import { LinkDetail } from "./LinkDetail";
import { TraceDialog } from "./TraceDialog";
import type { StateSnapshot, Selection, TracedPath } from "../types";

interface InfoPanelProps {
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  /** The anchor GS for Selected Pair Mode: when a sat is selected, open straight to its pair. */
  anchorGsId?: string | null;
  onSelect: (sel: Selection | null) => void;
  onTraceResult?: (path: TracedPath | null) => void;
}

export function InfoPanel({
  snapshot,
  selection,
  anchorGsId,
  onSelect,
  onTraceResult,
}: InfoPanelProps) {
  if (!snapshot) {
    return (
      <div className="info-panel">
        <h2>Waiting for data...</h2>
      </div>
    );
  }

  let detailSection: React.ReactNode;

  if (!selection) {
    detailSection = <NetworkSummary snapshot={snapshot} />;
  } else if (selection.type === "satellite") {
    const node = snapshot.nodes.find((n) => n.node_id === selection.id);
    detailSection = node ? (
      <SatelliteDetail
        node={node}
        snapshot={snapshot}
        anchorGsId={anchorGsId}
        onSelect={onSelect}
      />
    ) : (
      <NetworkSummary snapshot={snapshot} />
    );
  } else if (selection.type === "ground_station") {
    const node = snapshot.nodes.find((n) => n.node_id === selection.id);
    detailSection = node ? (
      <GroundStationDetail node={node} snapshot={snapshot} onSelect={onSelect} />
    ) : (
      <NetworkSummary snapshot={snapshot} />
    );
  } else if (selection.type === "link") {
    const link = snapshot.links.find(
      (l) => `${[l.node_a, l.node_b].sort().join(":")}` === selection.id,
    );
    detailSection = link ? (
      <LinkDetail link={link} snapshot={snapshot} />
    ) : (
      <NetworkSummary snapshot={snapshot} />
    );
  } else {
    detailSection = <NetworkSummary snapshot={snapshot} />;
  }

  return (
    <div className="info-panel">
      <div className="info-panel-detail">{detailSection}</div>
      {/* Trace path owns the space the events list used to occupy — it is a
          primary user tool and gets a stable, always-reachable home. */}
      <div className="info-panel-trace">
        <TraceDialog
          nodes={snapshot.nodes}
          selectedNodeId={selection?.type !== "link" ? selection?.id ?? null : null}
          onTraceResult={onTraceResult}
          snapshot={snapshot}
        />
      </div>
    </div>
  );
}
