/** Right panel — switches display based on selection type. */

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
}

export function InfoPanel({ snapshot, selection, onSelect }: InfoPanelProps) {
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
      <SatelliteDetail node={node} snapshot={snapshot} />
    ) : (
      <NetworkSummary snapshot={snapshot} onSelect={onSelect} />
    );
  } else if (selection.type === "ground_station") {
    const node = snapshot.nodes.find((n) => n.node_id === selection.id);
    detailSection = node ? (
      <GroundStationDetail node={node} snapshot={snapshot} />
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
    <div className="info-panel">
      {detailSection}
      <hr className="section-divider" />
      <EventLog events={snapshot.recent_events} onSelect={onSelect} />
    </div>
  );
}
