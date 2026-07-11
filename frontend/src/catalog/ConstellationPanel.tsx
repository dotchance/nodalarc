// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Constellation selection backed by VS-API catalog and authoring facts. */

import { useRef, useState } from "react";
import type {
  BuilderVisualWalkerLayoutRequest,
  BuilderVisualWalkerLayoutResult,
} from "../builder/generated/builderApi";
import type {
  ConstellationPreset,
  OrbitModel,
  WalkerPattern,
  WizardConstellationCapability,
  WizardConstellationGeometry,
} from "./wizardTypes";
import { CONSTELLATION_HELP } from "./wizardHelp";
import {
  constellationUnsupportedReason,
  supportedOrbitModelsForConstellation,
} from "./orbitModels";

function Help({ text }: { text: string | undefined }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <span className="wizard-help-wrap">
      <button
        className="wizard-help-btn"
        onClick={(event) => {
          event.preventDefault();
          setOpen(!open);
        }}
        aria-label="Help"
      >
        ?
      </button>
      {open && <span className="wizard-help-text">{text}</span>}
    </span>
  );
}

function identifierToken(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+/, "")
      .replace(/-+$/, "") || "custom"
  );
}

function numberToken(value: number): string {
  return identifierToken(String(value).replace(".", "-"));
}

function customPreset(
  geometry: WizardConstellationGeometry,
  capability: WizardConstellationCapability,
  defaultNode: string,
  patternLabel: string,
): ConstellationPreset {
  const name = `custom-${geometry.planes}x${geometry.slots_per_plane}-${numberToken(geometry.altitude_km)}km`;
  return {
    name,
    description: `${geometry.planes} planes × ${geometry.slots_per_plane} sats, ${geometry.altitude_km} km, ${geometry.inclination_deg}° ${patternLabel}`,
    satellite_count: geometry.planes * geometry.slots_per_plane,
    default_node: defaultNode,
    capability,
    constellation: null,
    custom_geometry: {
      ...geometry,
      display_name: name,
      description: `${geometry.planes} planes × ${geometry.slots_per_plane} sats, ${geometry.altitude_km} km, ${geometry.inclination_deg}° ${patternLabel}`,
    },
  };
}

function OrbitSupportBadges({
  preset,
  orbitModels,
}: {
  preset: ConstellationPreset;
  orbitModels: readonly OrbitModel[];
}) {
  const supportedModels = supportedOrbitModelsForConstellation(preset, orbitModels);
  return (
    <div className="wizard-card-orbit-support" aria-label="Supported orbit models">
      <span className="wizard-card-orbit-label">Orbit</span>
      {supportedModels.map((model) => (
        <span key={model.id} className="wizard-card-orbit-badge">
          {model.label}
        </span>
      ))}
      {supportedModels.length === 0 && (
        <span className="wizard-card-orbit-badge wizard-card-orbit-badge--disabled">
          No supported orbit model
        </span>
      )}
    </div>
  );
}

function CustomConstellationForm({
  onSubmit,
  onCancel,
  capability,
  seed,
  defaultNode,
  patterns,
  onDeriveLayout,
}: {
  onSubmit: (preset: ConstellationPreset) => void;
  onCancel: () => void;
  capability: WizardConstellationCapability;
  seed: WizardConstellationGeometry;
  defaultNode: string;
  patterns: readonly WalkerPattern[];
  onDeriveLayout: (
    intent: BuilderVisualWalkerLayoutRequest,
  ) => Promise<BuilderVisualWalkerLayoutResult>;
}) {
  const [form, setForm] = useState<WizardConstellationGeometry>({ ...seed });
  const [deriving, setDeriving] = useState(false);
  const [deriveError, setDeriveError] = useState<string | null>(null);
  const deriveSequence = useRef(0);
  const selectedPattern = patterns.find((pattern) => pattern.id === form.pattern);

  const requestDerivedLayout = (next: WizardConstellationGeometry) => {
    setForm(next);
    setDeriveError(null);
    setDeriving(true);
    deriveSequence.current += 1;
    const sequence = deriveSequence.current;
    const intent = {
      pattern: next.pattern,
      planes: next.planes,
      slots_per_plane: next.slots_per_plane,
    } satisfies BuilderVisualWalkerLayoutRequest;
    void onDeriveLayout(intent).then(
      (derived) => {
        if (deriveSequence.current !== sequence) return;
        setForm((current) =>
          current.pattern === intent.pattern &&
          current.planes === intent.planes &&
          current.slots_per_plane === intent.slots_per_plane
            ? { ...current, ...derived }
            : current,
        );
        setDeriving(false);
      },
      (cause) => {
        if (deriveSequence.current !== sequence) return;
        setDeriveError(cause instanceof Error ? cause.message : String(cause));
        setDeriving(false);
      },
    );
  };

  const setNumber = (
    field: Exclude<keyof WizardConstellationGeometry, "display_name" | "description" | "pattern">,
    rawValue: string,
  ) => {
    const value = Number(rawValue);
    if (!Number.isFinite(value)) return;
    const next = { ...form, [field]: value };
    if (field === "planes" || field === "slots_per_plane") {
      requestDerivedLayout(next);
    } else {
      deriveSequence.current += 1;
      setDeriving(false);
      setDeriveError(null);
      setForm(next);
    }
  };

  const setPattern = (pattern: WizardConstellationGeometry["pattern"]) => {
    requestDerivedLayout({ ...form, pattern });
  };

  return (
    <div className="wizard-custom-form">
      <h3 className="wizard-section-title">Orbital parameters</h3>
      <div className="wizard-custom-field">
        <label>Altitude (km) <Help text={CONSTELLATION_HELP.altitude_km} /></label>
        <input aria-label="Altitude (km)" type="number" min={160} max={40000}
          value={form.altitude_km}
          onChange={(event) => setNumber("altitude_km", event.target.value)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>{"Inclination (°)"} <Help text={CONSTELLATION_HELP.inclination_deg} /></label>
        <input aria-label="Inclination" type="number" min={0} max={180} step={0.1}
          value={form.inclination_deg}
          onChange={(event) => setNumber("inclination_deg", event.target.value)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>Pattern</label>
        <select aria-label="Pattern" value={form.pattern}
          onChange={(event) => setPattern(event.target.value as WizardConstellationGeometry["pattern"])}
          className="wizard-select">
          {patterns.map((pattern) => (
            <option key={pattern.id} value={pattern.id}>{pattern.label}</option>
          ))}
        </select>
        {selectedPattern && <div className="wizard-card-desc">{selectedPattern.description}</div>}
      </div>
      <div className="wizard-custom-field">
        <label>Orbital Planes <Help text={CONSTELLATION_HELP.planes} /></label>
        <input aria-label="Orbital Planes" type="number" min={2} max={72}
          value={form.planes}
          onChange={(event) => setNumber("planes", event.target.value)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>Satellites per Plane <Help text={CONSTELLATION_HELP.sats_per_plane} /></label>
        <input aria-label="Satellites per Plane" type="number" min={1} max={60}
          value={form.slots_per_plane}
          onChange={(event) => setNumber("slots_per_plane", event.target.value)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <span className="wizard-custom-computed">
          Total: {form.planes * form.slots_per_plane} satellites
        </span>
      </div>
      <div className="wizard-custom-field">
        <label>{"RAAN Spacing (°)"} <Help text={CONSTELLATION_HELP.raan_spacing_deg} /></label>
        <input aria-label="RAAN Spacing" type="number" min={0} max={360} step={0.1}
          value={form.raan_spacing_deg}
          onChange={(event) => setNumber("raan_spacing_deg", event.target.value)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>{"Phase Offset (°)"} <Help text={CONSTELLATION_HELP.phase_offset_deg} /></label>
        <input aria-label="Phase Offset" type="number" min={0} max={360} step={0.001}
          value={form.phase_offset_deg}
          onChange={(event) => setNumber("phase_offset_deg", event.target.value)}
          className="wizard-select" />
      </div>
      <div className="wizard-nav" style={{ marginTop: 16 }}>
        <button className="wizard-nav-btn" onClick={onCancel}>Cancel</button>
        <button className="wizard-nav-btn wizard-nav-btn--primary"
          disabled={deriving || deriveError !== null || selectedPattern === undefined}
          onClick={() => {
            if (selectedPattern) {
              onSubmit(customPreset(form, capability, defaultNode, selectedPattern.label));
            }
          }}>
          {deriving ? "Deriving layout…" : "Use Custom Constellation"}
        </button>
      </div>
      {deriveError && <div className="wizard-error">{deriveError}</div>}
    </div>
  );
}

interface ConstellationPanelProps {
  presets: ConstellationPreset[];
  customGeometryCapability: WizardConstellationCapability | null;
  customGeometrySeed: WizardConstellationGeometry | null;
  customGeometryDefaultNode: string | null;
  customGeometryPatterns: readonly WalkerPattern[];
  orbitModels: readonly OrbitModel[];
  selected: ConstellationPreset | null;
  onSelect: (preset: ConstellationPreset) => void;
  onDeriveLayout: (
    intent: BuilderVisualWalkerLayoutRequest,
  ) => Promise<BuilderVisualWalkerLayoutResult>;
}

export function ConstellationPanel({
  presets,
  customGeometryCapability,
  customGeometrySeed,
  customGeometryDefaultNode,
  customGeometryPatterns,
  orbitModels,
  selected,
  onSelect,
  onDeriveLayout,
}: ConstellationPanelProps) {
  const [showCustom, setShowCustom] = useState(false);
  const customFactsReady =
    customGeometryCapability !== null &&
    customGeometrySeed !== null &&
    customGeometryDefaultNode !== null &&
    customGeometryPatterns.length > 0;

  if (
    showCustom &&
    customGeometryCapability &&
    customGeometrySeed &&
    customGeometryDefaultNode
  ) {
    return (
      <CustomConstellationForm
        onSubmit={(preset) => {
          setShowCustom(false);
          onSelect(preset);
        }}
        onCancel={() => setShowCustom(false)}
        capability={customGeometryCapability}
        seed={customGeometrySeed}
        defaultNode={customGeometryDefaultNode}
        patterns={customGeometryPatterns}
        onDeriveLayout={onDeriveLayout}
      />
    );
  }

  if (presets.length === 0 || !customFactsReady || orbitModels.length === 0) {
    return (
      <div className="wizard-error">
        Constellation authoring facts did not load. The wizard cannot build a session
        without VS-API catalog and capability data.
      </div>
    );
  }

  return (
    <div className="wizard-grid">
      {[...presets].sort((a, b) => a.name.localeCompare(b.name)).map((preset) => {
        const disabledReason = constellationUnsupportedReason(preset);
        const disabled = disabledReason !== null;
        return (
          <button
            key={preset.name}
            className={`wizard-card ${selected?.name === preset.name ? "wizard-card--selected" : ""} ${disabled ? "wizard-card--disabled" : ""}`}
            onClick={() => !disabled && onSelect(preset)}
            disabled={disabled}
            title={disabledReason ?? undefined}
          >
            <div className="wizard-card-title">{preset.name}</div>
            <div className="wizard-card-stat">{preset.satellite_count} satellites</div>
            <OrbitSupportBadges preset={preset} orbitModels={orbitModels} />
            <div className="wizard-card-desc">{preset.description}</div>
            {disabledReason && <div className="wizard-card-disabled">{disabledReason}</div>}
          </button>
        );
      })}
      <button
        className="wizard-card wizard-card--custom"
        onClick={() =>
          !customGeometryCapability!.unavailable_reason && setShowCustom(true)
        }
        disabled={customGeometryCapability!.unavailable_reason !== null}
        title={customGeometryCapability!.unavailable_reason ?? undefined}
      >
        <div className="wizard-card-title">Custom</div>
        <div className="wizard-card-desc">
          Define custom orbital geometry with full control over altitude,
          inclination, plane count, and Walker pattern parameters.
        </div>
        {customGeometryCapability!.unavailable_reason && (
          <div className="wizard-card-disabled">
            {customGeometryCapability!.unavailable_reason}
          </div>
        )}
      </button>
    </div>
  );
}
