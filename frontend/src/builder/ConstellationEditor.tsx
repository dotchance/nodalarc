// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Constellation draft editor — the builder's first authoring surface.
 *
 *  Progressive disclosure like the read-only inspector: Orbit and Pattern
 *  cards, one open at a time, closed cards reading as the spec sheet. Every
 *  field is typeable; presets SEED raw values the user then owns (never
 *  modes). Orbit sanity findings warn in plain language and never block —
 *  the resolver's verdict arrives through the live resolve-check and is
 *  shown verbatim in the status bar.
 */

import { useState } from "react";
import { Button } from "../ui/Button";
import { useBuilderCatalog } from "./useBuilderWorld";
import {
  ORBIT_PRESETS,
  orbitWarnings,
  type DraftConstellation,
  type DraftOrbit,
} from "./workspace";

interface ConstellationEditorProps {
  draft: DraftConstellation;
  onUpdate: (patch: Partial<DraftConstellation>) => void;
  onUpdateOrbit: (patch: Partial<DraftOrbit>) => void;
  onRemove: () => void;
}

function NumberField({
  label,
  value,
  onChange,
  step = 1,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  step?: number;
  suffix?: string;
}) {
  return (
    <label className="builder-field">
      <span className="builder-field-label">{label}</span>
      <span className="builder-field-input">
        <input
          type="number"
          value={value}
          step={step}
          onChange={(e) => {
            const parsed = Number(e.target.value);
            if (Number.isFinite(parsed)) onChange(parsed);
          }}
        />
        {suffix && <span className="builder-field-suffix">{suffix}</span>}
      </span>
    </label>
  );
}

export function ConstellationEditor({
  draft,
  onUpdate,
  onUpdateOrbit,
  onRemove,
}: ConstellationEditorProps) {
  const [openCard, setOpenCard] = useState<string | null>("orbit");
  const nodes = useBuilderCatalog("nodes");
  const warnings = orbitWarnings(draft.orbit);
  const toggle = (id: string) => setOpenCard((prev) => (prev === id ? null : id));

  return (
    <div className="builder-inspector-stack" data-testid="builder-editor">
      <label className="builder-field">
        <span className="builder-field-label">name</span>
        <span className="builder-field-input">
          <input
            type="text"
            value={draft.display_name}
            onChange={(e) => onUpdate({ display_name: e.target.value })}
          />
        </span>
      </label>

      <div className={`builder-card${openCard === "orbit" ? " builder-card--open" : ""}`}>
        <button className="builder-card-head" onClick={() => toggle("orbit")}>
          <span className="builder-card-title">Orbit</span>
          <span className="builder-card-summary">
            {draft.orbit.shape_kind === "circular"
              ? `${Math.round(draft.orbit.altitude_km)} km circular`
              : `${Math.round(draft.orbit.perigee_altitude_km)} × ${Math.round(draft.orbit.apogee_altitude_km)} km`}{" "}
            · {draft.orbit.inclination_deg.toFixed(1)}°
          </span>
        </button>
        {openCard === "orbit" && (
          <div className="builder-card-body">
            <div className="builder-preset-row">
              {ORBIT_PRESETS.map((preset) => (
                <Button key={preset.label} onClick={() => onUpdateOrbit(preset.orbit)}>
                  {preset.label}
                </Button>
              ))}
            </div>
            <div className="builder-preset-row" role="radiogroup" aria-label="Orbit shape">
              <Button
                active={draft.orbit.shape_kind === "circular"}
                onClick={() => onUpdateOrbit({ shape_kind: "circular" })}
              >
                circular
              </Button>
              <Button
                active={draft.orbit.shape_kind === "elliptical"}
                onClick={() => onUpdateOrbit({ shape_kind: "elliptical" })}
              >
                elliptical
              </Button>
            </div>
            {draft.orbit.shape_kind === "circular" ? (
              <NumberField
                label="altitude"
                value={draft.orbit.altitude_km}
                step={10}
                suffix="km"
                onChange={(altitude_km) => onUpdateOrbit({ altitude_km })}
              />
            ) : (
              <>
                <NumberField
                  label="perigee"
                  value={draft.orbit.perigee_altitude_km}
                  step={10}
                  suffix="km"
                  onChange={(perigee_altitude_km) => onUpdateOrbit({ perigee_altitude_km })}
                />
                <NumberField
                  label="apogee"
                  value={draft.orbit.apogee_altitude_km}
                  step={10}
                  suffix="km"
                  onChange={(apogee_altitude_km) => onUpdateOrbit({ apogee_altitude_km })}
                />
                <NumberField
                  label="arg of perigee"
                  value={draft.orbit.argument_of_perigee_deg}
                  suffix="deg"
                  onChange={(argument_of_perigee_deg) =>
                    onUpdateOrbit({ argument_of_perigee_deg })
                  }
                />
              </>
            )}
            <NumberField
              label="inclination"
              value={draft.orbit.inclination_deg}
              suffix="deg"
              onChange={(inclination_deg) => onUpdateOrbit({ inclination_deg })}
            />
            <NumberField
              label="RAAN"
              value={draft.orbit.raan_deg}
              suffix="deg"
              onChange={(raan_deg) => onUpdateOrbit({ raan_deg })}
            />
            <NumberField
              label="mean anomaly"
              value={draft.orbit.mean_anomaly_deg}
              suffix="deg"
              onChange={(mean_anomaly_deg) => onUpdateOrbit({ mean_anomaly_deg })}
            />
            {warnings.map((warning) => (
              <div className="builder-warning" key={warning}>
                {warning}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className={`builder-card${openCard === "pattern" ? " builder-card--open" : ""}`}>
        <button className="builder-card-head" onClick={() => toggle("pattern")}>
          <span className="builder-card-title">Pattern</span>
          <span className="builder-card-summary">
            {draft.planes} × {draft.slots_per_plane} ={" "}
            {draft.planes * draft.slots_per_plane} sats
          </span>
        </button>
        {openCard === "pattern" && (
          <div className="builder-card-body">
            <NumberField
              label="planes"
              value={draft.planes}
              onChange={(planes) => onUpdate({ planes: Math.max(1, Math.round(planes)) })}
            />
            <NumberField
              label="slots per plane"
              value={draft.slots_per_plane}
              onChange={(slots_per_plane) =>
                onUpdate({ slots_per_plane: Math.max(1, Math.round(slots_per_plane)) })
              }
            />
            <NumberField
              label="RAAN spacing"
              value={draft.raan_spacing_deg}
              suffix="deg"
              onChange={(raan_spacing_deg) => onUpdate({ raan_spacing_deg })}
            />
            <NumberField
              label="phase offset"
              value={draft.phase_offset_deg}
              suffix="deg"
              onChange={(phase_offset_deg) => onUpdate({ phase_offset_deg })}
            />
          </div>
        )}
      </div>

      <div className={`builder-card${openCard === "node" ? " builder-card--open" : ""}`}>
        <button className="builder-card-head" onClick={() => toggle("node")}>
          <span className="builder-card-title">Node</span>
          <span className="builder-card-summary">
            {draft.node_ref.split("/").pop()?.replace(".yaml", "")}
          </span>
        </button>
        {openCard === "node" && (
          <div className="builder-card-body">
            <select
              aria-label="Node primitive"
              value={draft.node_ref}
              onChange={(e) => onUpdate({ node_ref: e.target.value })}
            >
              {nodes.entries
                .filter((entry) => !entry.error)
                .map((entry) => (
                  <option key={entry.ref} value={entry.ref}>
                    {entry.display_name ?? entry.id ?? entry.ref}
                  </option>
                ))}
            </select>
            {nodes.error && <div className="builder-warning">{nodes.error}</div>}
          </div>
        )}
      </div>

      <Button variant="danger" onClick={onRemove}>
        Remove constellation
      </Button>
    </div>
  );
}
