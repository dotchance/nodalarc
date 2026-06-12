// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Satellite primitive picker — which node flies the chosen constellation.
 *
 * Sessions assemble from primitives: the constellation supplies geometry and
 * a default node; any catalog space node can be composed in instead. The
 * default stays selectable so "no override" is an explicit, visible choice.
 */

import type { SatelliteTypePreset } from "./wizardTypes";

interface SatelliteTypePanelProps {
  satelliteTypes: SatelliteTypePreset[];
  selected: SatelliteTypePreset | null;
  /** The constellation's own node id, flown when nothing is selected. */
  defaultNode: string | null;
  onSelect: (preset: SatelliteTypePreset | null) => void;
}

function terminalSummary(preset: SatelliteTypePreset): string {
  return preset.terminals
    .map((t) => `${t.role ?? t.id} ×${t.count}`)
    .join(" · ");
}

export function SatelliteTypePanel({
  satelliteTypes,
  selected,
  defaultNode,
  onSelect,
}: SatelliteTypePanelProps) {
  if (satelliteTypes.length === 0) {
    return (
      <div className="wizard-error">
        Satellite primitives did not load. The wizard cannot compose a session
        without the node catalog from VS-API.
      </div>
    );
  }

  return (
    <div className="wizard-grid">
      <button
        className={`wizard-card ${selected === null ? "wizard-card--selected" : ""}`}
        onClick={() => onSelect(null)}
        title="Fly the node the constellation primitive declares"
      >
        <div className="wizard-card-title">Constellation default</div>
        <div className="wizard-card-stat">{defaultNode ?? "declared by constellation"}</div>
        <div className="wizard-card-desc">
          Use the node the selected constellation already flies.
        </div>
      </button>
      {satelliteTypes.map((preset) => (
        <button
          key={preset.name}
          className={`wizard-card ${selected?.name === preset.name ? "wizard-card--selected" : ""}`}
          onClick={() => onSelect(preset)}
        >
          <div className="wizard-card-title">{preset.display_name}</div>
          <div className="wizard-card-stat">{terminalSummary(preset)}</div>
          <div className="wizard-card-desc">{preset.notes}</div>
        </button>
      ))}
    </div>
  );
}
