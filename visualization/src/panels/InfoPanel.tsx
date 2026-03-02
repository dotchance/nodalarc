/** Right panel — switches display based on selection type.
 *  Includes a draggable divider between detail section and event log.
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { NetworkSummary } from "./NetworkSummary";
import { SatelliteDetail } from "./SatelliteDetail";
import { GroundStationDetail } from "./GroundStationDetail";
import { LinkDetail } from "./LinkDetail";
import { EventLog } from "./EventLog";
import type { StateSnapshot, Selection } from "../types";

interface InfoPanelProps {
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  onFlyTo?: (nodeId: string) => void;
}

export function InfoPanel({ snapshot, selection, onSelect, onFlyTo }: InfoPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [splitPct, setSplitPct] = useState(50); // percentage for detail section
  const draggingRef = useRef(false);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    draggingRef.current = true;
    e.preventDefault();
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current || !panelRef.current) return;
      const rect = panelRef.current.getBoundingClientRect();
      const pct = ((e.clientY - rect.top) / rect.height) * 100;
      setSplitPct(Math.max(15, Math.min(85, pct)));
    };
    const onUp = () => { draggingRef.current = false; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  if (!snapshot) {
    return (
      <div className="info-panel">
        <h2>Waiting for data...</h2>
      </div>
    );
  }

  let detailSection: React.ReactNode;

  if (!selection) {
    detailSection = <NetworkSummary snapshot={snapshot} onSelect={onSelect} />;
  } else if (selection.type === "satellite") {
    const node = snapshot.nodes.find((n) => n.node_id === selection.id);
    detailSection = node ? (
      <SatelliteDetail node={node} snapshot={snapshot} onSelect={onSelect} />
    ) : (
      <NetworkSummary snapshot={snapshot} onSelect={onSelect} />
    );
  } else if (selection.type === "ground_station") {
    const node = snapshot.nodes.find((n) => n.node_id === selection.id);
    detailSection = node ? (
      <GroundStationDetail node={node} snapshot={snapshot} onSelect={onSelect} />
    ) : (
      <NetworkSummary snapshot={snapshot} onSelect={onSelect} />
    );
  } else if (selection.type === "link") {
    const link = snapshot.links.find(
      (l) => `${[l.node_a, l.node_b].sort().join(":")}` === selection.id,
    );
    detailSection = link ? (
      <LinkDetail link={link} snapshot={snapshot} />
    ) : (
      <NetworkSummary snapshot={snapshot} onSelect={onSelect} />
    );
  } else {
    detailSection = <NetworkSummary snapshot={snapshot} onSelect={onSelect} />;
  }

  return (
    <div className="info-panel" ref={panelRef}>
      <div style={{ flex: `0 0 ${splitPct}%`, overflow: "auto", minHeight: 0 }}>
        {detailSection}
      </div>
      <div
        className="panel-divider"
        onMouseDown={handleDragStart}
      />
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <EventLog events={snapshot.recent_events} onSelect={onSelect} onFlyTo={onFlyTo} />
      </div>
    </div>
  );
}
