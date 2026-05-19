// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Step Group A — independent selection cards.
 *
 * Constellation, satellite type, ground stations, and orbit model can be
 * selected in any order. Each card shows a summary when collapsed and expands
 * to show the full selection panel when clicked. The Preview Coverage button
 * activates when the topology inputs are selected.
 */

import { useState } from "react";
import type {
  ConstellationPreset,
  SatelliteTypePreset,
  GroundStationSet,
  AvailableStation,
  ActiveCard,
  OrbitPropagator,
} from "./wizardTypes";
import type { SessionInfo } from "../types";
import { SatelliteTypePanel } from "./SatelliteTypePanel";
import { GroundStationPanel } from "./GroundStationPanel";
import { ConstellationPanel } from "./ConstellationPanel";
import { OrbitModelPanel } from "./OrbitModelPanel";
import { ORBIT_MODEL_OPTIONS } from "./orbitModels";

interface SelectionCardsProps {
  // Data
  presets: ConstellationPreset[];
  satelliteTypes: SatelliteTypePreset[];
  groundStationSets: GroundStationSet[];
  availableStations: AvailableStation[];
  // Current selections
  constellation: ConstellationPreset | null;
  satelliteType: SatelliteTypePreset | null;
  groundStationSet: GroundStationSet | null;
  orbitPropagator: OrbitPropagator;
  // Callbacks
  onSelectConstellation: (preset: ConstellationPreset) => void;
  onSelectSatelliteType: (preset: SatelliteTypePreset) => void;
  onSelectGroundStationSet: (set: GroundStationSet) => void;
  onSelectCustomGroundStations: (names: string[]) => void;
  onSelectOrbitPropagator: (model: OrbitPropagator) => void;
  onPreview: () => void;
  onContinueWithoutPreview: () => void;
  // Preview state
  canPreview: boolean;
  previewing: boolean;
  // Fallback (passed through to ConstellationPanel)
  fallbackSessions: SessionInfo[];
  deploying: boolean;
  onFallbackDeploy: (id: string) => void;
}

type CardId = "constellation" | "satellite-type" | "ground-stations" | "orbit-model";

const CARDS: { id: CardId; label: string; getStatus: (props: SelectionCardsProps) => string | null }[] = [
  {
    id: "constellation",
    label: "Constellation",
    getStatus: (p) => p.constellation ? `${p.constellation.name} (${p.constellation.satellite_count} sats)` : null,
  },
  {
    id: "satellite-type",
    label: "Satellite Type",
    getStatus: (p) => p.satelliteType?.name ?? null,
  },
  {
    id: "ground-stations",
    label: "Ground Stations",
    getStatus: (p) => p.groundStationSet ? `${p.groundStationSet.name} (${p.groundStationSet.stations.length})` : null,
  },
  {
    id: "orbit-model",
    label: "Orbit Model",
    getStatus: (p) => ORBIT_MODEL_OPTIONS.find((o) => o.id === p.orbitPropagator)?.label ?? p.orbitPropagator,
  },
];

export function SelectionCards(props: SelectionCardsProps) {
  const [activeCard, setActiveCard] = useState<ActiveCard>("constellation");

  const toggleCard = (id: CardId) => {
    setActiveCard((prev) => (prev === id ? null : id));
  };

  const allSelected = props.constellation !== null
    && props.satelliteType !== null
    && props.groundStationSet !== null;

  return (
    <div className="wizard-panel">
      {/* Card summary row */}
      <div className="wizard-selection-cards">
        {CARDS.map(({ id, label, getStatus }) => {
          const status = getStatus(props);
          return (
            <button
              key={id}
              className={`wizard-selection-card ${activeCard === id ? "wizard-selection-card--active" : ""} ${status ? "wizard-selection-card--done" : ""}`}
              onClick={() => toggleCard(id)}
            >
              <div className="wizard-selection-card-label">{label}</div>
              <div className="wizard-selection-card-status">
                {status ?? "Not selected"}
              </div>
            </button>
          );
        })}
      </div>

      {/* Expanded selection panel */}
      {activeCard === "constellation" && (
        <div className="wizard-selection-panel">
          <h2 className="wizard-panel-title">Select Constellation</h2>
          <ConstellationPanel
            presets={props.presets}
            selected={props.constellation}
            onSelect={(p) => { props.onSelectConstellation(p); setActiveCard(null); }}
            fallbackSessions={props.fallbackSessions}
            deploying={props.deploying}
            onFallbackDeploy={props.onFallbackDeploy}
          />
        </div>
      )}

      {activeCard === "satellite-type" && (
        <div className="wizard-selection-panel">
          <h2 className="wizard-panel-title">Select Satellite Type</h2>
          <SatelliteTypePanel
            satelliteTypes={props.satelliteTypes}
            selected={props.satelliteType}
            onSelect={(p) => { props.onSelectSatelliteType(p); setActiveCard(null); }}
          />
        </div>
      )}

      {activeCard === "ground-stations" && (
        <div className="wizard-selection-panel">
          <h2 className="wizard-panel-title">Select Ground Stations</h2>
          <GroundStationPanel
            groundStationSets={props.groundStationSets}
            availableStations={props.availableStations}
            selected={props.groundStationSet}
            onSelectSet={(s) => { props.onSelectGroundStationSet(s); setActiveCard(null); }}
            onSelectCustom={(names) => { props.onSelectCustomGroundStations(names); setActiveCard(null); }}
          />
        </div>
      )}

      {activeCard === "orbit-model" && (
        <div className="wizard-selection-panel">
          <h2 className="wizard-panel-title">Select Orbit Model</h2>
          <OrbitModelPanel
            constellation={props.constellation}
            selected={props.orbitPropagator}
            onSelect={(m) => { props.onSelectOrbitPropagator(m); setActiveCard(null); }}
          />
        </div>
      )}

      {/* Hint + action buttons */}
      <div className="wizard-selection-actions">
        {!allSelected && (
          <p className="wizard-hint">
            Select constellation, satellite type, and ground stations to preview coverage
          </p>
        )}
        {allSelected && (
          <>
            <button
              className="wizard-nav-btn wizard-nav-btn--primary"
              onClick={props.onPreview}
              disabled={!props.canPreview || props.previewing}
            >
              {props.previewing ? "Computing..." : "Preview Coverage"}
            </button>
            <button
              className="wizard-nav-btn"
              onClick={props.onContinueWithoutPreview}
            >
              Skip Preview
            </button>
          </>
        )}
      </div>
    </div>
  );
}
