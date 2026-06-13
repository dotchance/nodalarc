// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Constellation selection panel — library presets + custom orbital geometry builder.
 *
 * Custom mode lets the user define orbital geometry with any parameters.
 * Help callouts explain each parameter in plain English.
 */

import { useState } from "react";
import type { ConstellationPreset } from "./wizardTypes";
import { CONSTELLATION_HELP } from "./wizardHelp";
import { constellationUnsupportedReason, supportedOrbitModelsForConstellation } from "./orbitModels";

// --- Help component — click to expand, not hover tooltip ---

function Help({ text }: { text: string | undefined }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <span className="wizard-help-wrap">
      <button
        className="wizard-help-btn"
        onClick={(e) => { e.preventDefault(); setOpen(!open); }}
        aria-label="Help"
      >
        ?
      </button>
      {open && <span className="wizard-help-text">{text}</span>}
    </span>
  );
}

// --- Custom constellation form ---

interface CustomConstellationState {
  altitude_km: number;
  inclination_deg: number;
  pattern: "walker-delta" | "walker-star";
  planes: number;
  sats_per_plane: number;
  raan_spacing_deg: number;
  phase_offset_deg: number;
}

const MAX_ORBITAL_PLANES = 72;
const MAX_SATS_PER_PLANE = 60;

function roundDegrees(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function derivedRaanSpacing(planes: number): number {
  return roundDegrees(360 / planes);
}

function derivedPhaseOffset(planes: number, satsPerPlane: number): number {
  return roundDegrees(360 / (planes * satsPerPlane));
}

/** Geometry templates based on real LEO constellation filings.
 *
 * Altitude, inclination, and pattern are from FCC/ITU filings.
 * Plane counts and sats-per-plane are lab-scale representations —
 * real constellations have 12-72 planes with hundreds of satellites.
 * These templates preserve the orbital geometry character while being
 * deployable on a single K3s node.
 *
 * Sources: FCC IBFS filings (Starlink SAT-MOD-20200417-00037, Kuiper
 * SAT-LOA-20190704-00057), ITU filings (OneWeb), SDA fact sheets.
 */
/** Lab-scale geometry templates based on real LEO constellation filings.
 *
 * Scaling approach:
 * - Real plane count preserved when <= 8 (Iridium 6, Telesat 6, SDA 6)
 * - Large constellations (Starlink 72, Kuiper 28-36) scaled to 6-8 planes
 * - Sats-per-plane scaled to keep total between 48-100
 * - Phase offset recomputed: 360 / (planes × sats_per_plane) for uniform spacing
 * - Altitude, inclination, and pattern are exact from filings
 */
const GEOMETRY_PRESETS: { label: string; desc: string; basis: string; values: Partial<CustomConstellationState> }[] = [
  {
    label: "Starlink (53\u00b0 shell)",
    desc: "550 km, 53\u00b0, Walker-delta. Primary Starlink shell covering most populated latitudes.",
    basis: "72 planes \u00d7 22 = 1,584 sats",
    values: { altitude_km: 550, inclination_deg: 53, pattern: "walker-delta", planes: 8, sats_per_plane: 11 },
  },
  {
    label: "Starlink (576-node mesh)",
    desc: "550 km, 53\u00b0, Walker-delta. Historical NodalArc Starlink mesh scale test.",
    basis: "48 planes \u00d7 12 = 576 sats",
    values: { altitude_km: 550, inclination_deg: 53, pattern: "walker-delta", planes: 48, sats_per_plane: 12 },
  },
  {
    label: "Starlink (70\u00b0 shell)",
    desc: "570 km, 70\u00b0, Walker-delta. Higher-inclination shell for extended latitude coverage.",
    basis: "36 planes \u00d7 20 = 720 sats",
    values: { altitude_km: 570, inclination_deg: 70, pattern: "walker-delta", planes: 6, sats_per_plane: 11 },
  },
  {
    label: "Starlink (97.6\u00b0 polar)",
    desc: "560 km, 97.6\u00b0, sun-synchronous. Polar shell for global coverage including poles.",
    basis: "6 planes \u00d7 58 = 348 sats",
    values: { altitude_km: 560, inclination_deg: 97.6, pattern: "walker-star", planes: 6, sats_per_plane: 12 },
  },
  {
    label: "Kuiper (51.9\u00b0 shell)",
    desc: "630 km, 51.9\u00b0, Walker-delta. Amazon Kuiper\u2019s highest-inclination shell.",
    basis: "34 planes \u00d7 34 = 1,156 sats",
    values: { altitude_km: 630, inclination_deg: 51.9, pattern: "walker-delta", planes: 6, sats_per_plane: 11 },
  },
  {
    label: "Kuiper (42\u00b0 shell)",
    desc: "610 km, 42\u00b0, Walker-delta. Kuiper mid-inclination shell for tropical coverage.",
    basis: "36 planes \u00d7 36 = 1,296 sats",
    values: { altitude_km: 610, inclination_deg: 42, pattern: "walker-delta", planes: 6, sats_per_plane: 10 },
  },
  {
    label: "OneWeb",
    desc: "1,200 km, 87.9\u00b0, near-polar. Higher altitude gives wider per-satellite footprint.",
    basis: "12 planes \u00d7 49 = 588 sats",
    values: { altitude_km: 1200, inclination_deg: 87.9, pattern: "walker-star", planes: 6, sats_per_plane: 10 },
  },
  {
    label: "Iridium NEXT",
    desc: "780 km, 86.4\u00b0, Walker-star. Classic polar constellation with dramatic polar seam.",
    basis: "6 planes \u00d7 11 = 66 sats",
    values: { altitude_km: 780, inclination_deg: 86.4, pattern: "walker-star", planes: 6, sats_per_plane: 11 },
  },
  {
    label: "Telesat Lightspeed (polar)",
    desc: "1,015 km, 98.98\u00b0, sun-synchronous. Telesat\u2019s polar shell for enterprise connectivity.",
    basis: "6 planes \u00d7 13 = 78 sats",
    values: { altitude_km: 1015, inclination_deg: 98.98, pattern: "walker-star", planes: 6, sats_per_plane: 13 },
  },
  {
    label: "SDA Transport (Tranche 1)",
    desc: "~1,000 km, ~80\u00b0, near-polar. PWSA transport layer for military mesh networking.",
    basis: "6 planes \u00d7 21 = 126 sats",
    values: { altitude_km: 1000, inclination_deg: 80, pattern: "walker-star", planes: 6, sats_per_plane: 15 },
  },
  {
    label: "Globalstar",
    desc: "1,414 km, 52\u00b0, Walker-delta. Inclined LEO, bent-pipe architecture.",
    basis: "8 planes \u00d7 6 = 48 sats",
    values: { altitude_km: 1414, inclination_deg: 52, pattern: "walker-delta", planes: 8, sats_per_plane: 6 },
  },
];

const DEFAULTS: CustomConstellationState = {
  altitude_km: 550,
  inclination_deg: 53,
  pattern: "walker-delta",
  planes: 4,
  sats_per_plane: 11,
  raan_spacing_deg: 90,
  phase_offset_deg: derivedPhaseOffset(4, 11),
};

const CUSTOM_CONSTELLATION_NODE_REF = "nodalarc:nodes/space/starlink-v2-mesh.yaml";
const CUSTOM_CONSTELLATION_DEFAULT_NODE = "starlink-v2-mesh";
const CUSTOM_ORBIT_EPOCH = "2026-06-08T00:00:00Z";

function identifierToken(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+/, "")
    .replace(/-+$/, "")
    || "custom";
}

function numberToken(value: number): string {
  return identifierToken(String(value).replace(".", "-"));
}

function phasingMode(pattern: CustomConstellationState["pattern"]): "walker_delta" | "walker_star" {
  return pattern === "walker-star" ? "walker_star" : "walker_delta";
}

function withDerivedSpacing(
  values: Partial<CustomConstellationState>,
): CustomConstellationState {
  const v = { ...DEFAULTS, ...values };
  return {
    ...v,
    raan_spacing_deg: values.raan_spacing_deg ?? derivedRaanSpacing(v.planes),
    phase_offset_deg: values.phase_offset_deg ?? derivedPhaseOffset(v.planes, v.sats_per_plane),
  };
}

/** Convert geometry values to a catalog-grammar constellation preset. */
function geometryToPreset(label: string, desc: string, values: Partial<CustomConstellationState>): ConstellationPreset {
  const v = withDerivedSpacing(values);
  const total = v.planes * v.sats_per_plane;
  const name = identifierToken(label);
  const orbitId = `${name}-orbit-${numberToken(v.altitude_km)}km-${numberToken(v.inclination_deg)}deg`;
  return {
    name,
    description: desc,
    satellite_count: total,
    mode: "constellation",
    default_node: CUSTOM_CONSTELLATION_DEFAULT_NODE,
    constellation: JSON.stringify({
      constellation: {
        id: name,
        display_name: label,
        node: CUSTOM_CONSTELLATION_NODE_REF,
        orbit: {
          orbit: {
            id: orbitId,
            central_body: "nodalarc:bodies/earth.yaml",
            epoch: CUSTOM_ORBIT_EPOCH,
            shape: {
              altitude_km: v.altitude_km,
            },
            orientation: {
              inclination_deg: v.inclination_deg,
              raan_deg: 0,
              argument_of_perigee_deg: 0,
            },
            phase: {
              mean_anomaly_deg: 0,
            },
            propagator: "j2_mean_elements",
            reference: "user-authored",
            notes: `Custom ${v.altitude_km} km, ${v.inclination_deg} degree orbit generated by the session builder.`,
          },
        },
        planes: {
          count: v.planes,
          raan_spacing_deg: v.raan_spacing_deg,
        },
        slots_per_plane: v.sats_per_plane,
        phasing: {
          mode: phasingMode(v.pattern),
          phase_offset_deg: v.phase_offset_deg,
        },
        node_tags: [
          { tag: "all" },
        ],
        reference: "user-authored",
        notes: desc,
      },
    }),
    ground_stations: "",
  };
}

function OrbitSupportBadges({ preset }: { preset: ConstellationPreset }) {
  const supportedModels = supportedOrbitModelsForConstellation(preset);
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
          Coming Soon
        </span>
      )}
    </div>
  );
}

/** Pre-built ConstellationPreset cards from real constellation geometries. */
const REAL_WORLD_PRESETS: (ConstellationPreset & { basis: string })[] = GEOMETRY_PRESETS.map(
  (p) => ({ ...geometryToPreset(p.label, p.desc, p.values), basis: p.basis }),
);

function CustomConstellationForm({ onSubmit, onCancel }: {
  onSubmit: (preset: ConstellationPreset) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<CustomConstellationState>({ ...DEFAULTS });
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);

  const set = (field: string, value: string | number | boolean) => {
    setForm((prev) => {
      const next = { ...prev, [field]: value };
      if (field === "planes" && typeof value === "number" && value > 0) {
        next.raan_spacing_deg = derivedRaanSpacing(value);
        next.phase_offset_deg = derivedPhaseOffset(value, next.sats_per_plane);
      }
      if (field === "sats_per_plane" && typeof value === "number" && value > 0) {
        next.phase_offset_deg = derivedPhaseOffset(next.planes, value);
      }
      return next;
    });
    setFormError(null);
  };

  const applyPreset = (label: string, values: Partial<CustomConstellationState>) => {
    setForm(() => withDerivedSpacing(values));
    setFormError(null);
    setSelectedTemplate(label);
  };

  const handleSubmit = () => {
    if (form.altitude_km < 160) { setFormError("Altitude must be at least 160 km"); return; }
    if (form.altitude_km > 40000) { setFormError("Altitude must be under 40,000 km"); return; }
    if (form.inclination_deg < 0 || form.inclination_deg > 180) { setFormError("Inclination must be 0\u2013180\u00b0"); return; }
    if (form.planes < 1 || form.planes > MAX_ORBITAL_PLANES) { setFormError(`Planes must be 1\u2013${MAX_ORBITAL_PLANES}`); return; }
    if (form.sats_per_plane < 1 || form.sats_per_plane > MAX_SATS_PER_PLANE) { setFormError(`Satellites per plane must be 1\u2013${MAX_SATS_PER_PLANE}`); return; }

    const name = `custom-${form.planes}x${form.sats_per_plane}-${numberToken(form.altitude_km)}km`;
    const preset = geometryToPreset(
      name,
      `${form.planes} planes \u00d7 ${form.sats_per_plane} sats, ${form.altitude_km} km, ${form.inclination_deg}\u00b0 ${form.pattern}`,
      form,
    );
    onSubmit(preset);
  };

  return (
    <div className="wizard-custom-form">
      {/* Quick-start geometry presets */}
      <h3 className="wizard-section-title">Start from a template</h3>
      <div className="wizard-grid" style={{ marginBottom: 20 }}>
        {[...GEOMETRY_PRESETS].sort((a, b) => a.label.localeCompare(b.label)).map((p) => (
          <button
            key={p.label}
            className={`wizard-card wizard-card--compact ${selectedTemplate === p.label ? "wizard-card--selected" : ""}`}
            onClick={() => applyPreset(p.label, p.values)}
          >
            <div className="wizard-card-title">{p.label}</div>
            <div className="wizard-card-desc">{p.desc}</div>
            <div className="wizard-card-real">Basis: {p.basis}</div>
          </button>
        ))}
      </div>
      <h3 className="wizard-section-title">Orbital parameters</h3>

      <div className="wizard-custom-field">
        <label>Altitude (km) <Help text={CONSTELLATION_HELP.altitude_km} /></label>
        <input aria-label="Altitude (km)" type="number" min={160} max={40000} value={form.altitude_km}
          onChange={(e) => set("altitude_km", parseFloat(e.target.value) || 550)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>{"Inclination (\u00b0)"} <Help text={CONSTELLATION_HELP.inclination_deg} /></label>
        <input aria-label="Inclination" type="number" min={0} max={180} step={0.1} value={form.inclination_deg}
          onChange={(e) => set("inclination_deg", parseFloat(e.target.value) || 53)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>Pattern <Help text={CONSTELLATION_HELP.pattern} /></label>
        <select value={form.pattern} onChange={(e) => set("pattern", e.target.value)} className="wizard-select">
          <option value="walker-delta">Walker-delta (co-rotating planes)</option>
          <option value="walker-star">Walker-star (counter-rotating planes)</option>
        </select>
      </div>
      <div className="wizard-custom-field">
        <label>Orbital Planes <Help text={CONSTELLATION_HELP.planes} /></label>
        <input aria-label="Orbital Planes" type="number" min={1} max={MAX_ORBITAL_PLANES} value={form.planes}
          onChange={(e) => set("planes", parseInt(e.target.value) || 4)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>Satellites per Plane <Help text={CONSTELLATION_HELP.sats_per_plane} /></label>
        <input aria-label="Satellites per Plane" type="number" min={1} max={MAX_SATS_PER_PLANE} value={form.sats_per_plane}
          onChange={(e) => set("sats_per_plane", parseInt(e.target.value) || 11)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <span className="wizard-custom-computed">
          Total: {form.planes * form.sats_per_plane} satellites
        </span>
      </div>
      <div className="wizard-custom-field">
        <label>{"RAAN Spacing (\u00b0)"} <Help text={CONSTELLATION_HELP.raan_spacing_deg} /></label>
        <input aria-label="RAAN Spacing" type="number" min={0.1} max={360} step={0.1} value={form.raan_spacing_deg}
          onChange={(e) => set("raan_spacing_deg", parseFloat(e.target.value) || 90)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>{"Phase Offset (\u00b0)"} <Help text={CONSTELLATION_HELP.phase_offset_deg} /></label>
        <input aria-label="Phase Offset" type="number" min={0} max={360} step={0.001} value={form.phase_offset_deg}
          onChange={(e) => set("phase_offset_deg", parseFloat(e.target.value) || 0)}
          className="wizard-select" />
      </div>
      {formError && <div className="wizard-error" style={{ marginTop: 12 }}>{formError}</div>}

      <div className="wizard-nav" style={{ marginTop: 16 }}>
        <button className="wizard-nav-btn" onClick={onCancel}>Cancel</button>
        <button className="wizard-nav-btn wizard-nav-btn--primary" onClick={handleSubmit}>
          Use Custom Constellation
        </button>
      </div>
    </div>
  );
}

// --- Public panel component ---

interface ConstellationPanelProps {
  presets: ConstellationPreset[];
  selected: ConstellationPreset | null;
  onSelect: (preset: ConstellationPreset) => void;
}

export function ConstellationPanel({
  presets,
  selected,
  onSelect,
}: ConstellationPanelProps) {
  const [showCustom, setShowCustom] = useState(false);

  if (showCustom) {
    return (
      <CustomConstellationForm
        onSubmit={(preset) => { setShowCustom(false); onSelect(preset); }}
        onCancel={() => setShowCustom(false)}
      />
    );
  }

  if (presets.length === 0) {
    return (
      <div className="wizard-error">
        Constellation presets did not load. The wizard cannot build a session without
        the preset catalog from VS-API.
      </div>
    );
  }

  // Merge library and real-world presets, sort alphabetically
  const allPresets = [
    ...presets.map((p) => ({ ...p, basis: undefined as string | undefined })),
    ...REAL_WORLD_PRESETS,
  ].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="wizard-grid">
      {allPresets.map((p) => {
        const disabledReason = constellationUnsupportedReason(p);
        const disabled = disabledReason !== null;
        return (
          <button
            key={p.name}
            className={`wizard-card ${selected?.name === p.name ? "wizard-card--selected" : ""} ${disabled ? "wizard-card--disabled" : ""}`}
            onClick={() => !disabled && onSelect(p)}
            disabled={disabled}
            title={disabledReason ?? undefined}
          >
            <div className="wizard-card-title">{p.name}</div>
            <div className="wizard-card-stat">{p.satellite_count} satellites</div>
            <OrbitSupportBadges preset={p} />
            <div className="wizard-card-desc">{p.description}</div>
            {disabledReason && <div className="wizard-card-disabled">{disabledReason}</div>}
            {p.basis && <div className="wizard-card-real">Basis: {p.basis}</div>}
          </button>
        );
      })}
      <button
        className="wizard-card wizard-card--custom"
        onClick={() => setShowCustom(true)}
      >
        <div className="wizard-card-title">Custom</div>
        <div className="wizard-card-desc">
          Define custom orbital geometry with full control over altitude,
          inclination, plane count, and Walker pattern parameters.
        </div>
      </button>
    </div>
  );
}
