/** Ground station selection panel — library sets + custom station picker.
 *
 * Extracted from SessionWizard.tsx with zero behavior change.
 */

import { useState } from "react";
import type { GroundStationSet, AvailableStation } from "./wizardTypes";

// --- Custom ground station picker ---

function CustomGroundStationsForm({ stations, onSubmit, onCancel }: {
  stations: AvailableStation[];
  onSubmit: (selected: string[]) => void;
  onCancel: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(stations.map((s) => s.name)));
  const selectNone = () => setSelected(new Set());

  if (stations.length === 0) {
    return (
      <div className="wizard-custom-form">
        <p className="wizard-loading">Loading available stations...</p>
        <div className="wizard-nav" style={{ marginTop: 16 }}>
          <button className="wizard-nav-btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div className="wizard-custom-form">
      <div className="wizard-custom-gs-actions">
        <button className="wizard-nav-btn" onClick={selectAll}>Select All</button>
        <button className="wizard-nav-btn" onClick={selectNone}>Select None</button>
        <span className="wizard-custom-gs-count">{selected.size} of {stations.length} selected</span>
      </div>
      <div className="wizard-custom-gs-grid">
        {stations.map((s) => (
          <label key={s.name} className={`wizard-custom-gs-item ${selected.has(s.name) ? "wizard-custom-gs-item--selected" : ""}`}>
            <input type="checkbox" checked={selected.has(s.name)} onChange={() => toggle(s.name)} />
            <div>
              <div className="wizard-custom-gs-name">{s.name}</div>
              <div className="wizard-custom-gs-coords">{s.lat_deg.toFixed(1)}, {s.lon_deg.toFixed(1)}</div>
            </div>
          </label>
        ))}
      </div>
      <div className="wizard-nav" style={{ marginTop: 16 }}>
        <button className="wizard-nav-btn" onClick={onCancel}>Cancel</button>
        <button
          className="wizard-nav-btn wizard-nav-btn--primary"
          onClick={() => onSubmit(Array.from(selected))}
          disabled={selected.size === 0}
        >
          Use {selected.size} Station{selected.size !== 1 ? "s" : ""}
        </button>
      </div>
    </div>
  );
}

// --- Public panel component ---

interface GroundStationPanelProps {
  groundStationSets: GroundStationSet[];
  availableStations: AvailableStation[];
  selected: GroundStationSet | null;
  onSelectSet: (set: GroundStationSet) => void;
  onSelectCustom: (stationNames: string[]) => void;
}

export function GroundStationPanel({
  groundStationSets,
  availableStations,
  selected,
  onSelectSet,
  onSelectCustom,
}: GroundStationPanelProps) {
  const [showCustom, setShowCustom] = useState(false);

  if (showCustom) {
    return (
      <CustomGroundStationsForm
        stations={availableStations}
        onSubmit={(names) => { setShowCustom(false); onSelectCustom(names); }}
        onCancel={() => setShowCustom(false)}
      />
    );
  }

  if (groundStationSets.length === 0) {
    return <div className="wizard-loading"><p>Loading ground station sets...</p></div>;
  }

  return (
    <div className="wizard-grid">
      {groundStationSets.map((gs) => (
        <button
          key={gs.name}
          className={`wizard-card ${selected?.name === gs.name ? "wizard-card--selected" : ""}`}
          onClick={() => onSelectSet(gs)}
        >
          <div className="wizard-card-title">{gs.name}</div>
          <div className="wizard-card-stat">{gs.stations.length} stations</div>
          <div className="wizard-card-desc">{gs.description}</div>
          <div className="wizard-station-list">{gs.stations.join(", ")}</div>
        </button>
      ))}
      <button
        className="wizard-card wizard-card--custom"
        onClick={() => setShowCustom(true)}
      >
        <div className="wizard-card-title">Custom</div>
        <div className="wizard-card-desc">
          Pick individual ground stations from the available {availableStations.length} locations to build a custom set.
        </div>
      </button>
    </div>
  );
}
