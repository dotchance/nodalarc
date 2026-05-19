// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Constellation selection panel — library presets + custom orbital geometry builder.
 *
 * Custom mode lets the user define orbital geometry with any parameters.
 * Help callouts explain each parameter in plain English.
 */

import { useState } from "react";
import type { ConstellationPreset } from "./wizardTypes";
import type { SessionInfo } from "../types";
import { CONSTELLATION_HELP } from "./wizardHelp";

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
  pattern: string;
  planes: number;
  sats_per_plane: number;
  raan_spacing_deg: number;
  phase_offset_deg: number;
  polar_seam_enabled: boolean;
  polar_seam_lat: number;
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
const GEOMETRY_PRESETS: { label: string; desc: string; real: string; values: Partial<CustomConstellationState> }[] = [
  {
    label: "Starlink (53\u00b0 shell)",
    desc: "550 km, 53\u00b0, Walker-delta. Primary Starlink shell covering most populated latitudes.",
    real: "72 planes \u00d7 22 = 1,584 sats",
    values: { altitude_km: 550, inclination_deg: 53, pattern: "walker-delta", planes: 8, sats_per_plane: 11, phase_offset_deg: 4.1, polar_seam_enabled: false },
  },
  {
    label: "Starlink (70\u00b0 shell)",
    desc: "570 km, 70\u00b0, Walker-delta. Higher-inclination shell for extended latitude coverage.",
    real: "36 planes \u00d7 20 = 720 sats",
    values: { altitude_km: 570, inclination_deg: 70, pattern: "walker-delta", planes: 6, sats_per_plane: 11, phase_offset_deg: 5.45, polar_seam_enabled: false },
  },
  {
    label: "Starlink (97.6\u00b0 polar)",
    desc: "560 km, 97.6\u00b0, sun-synchronous. Polar shell for global coverage including poles.",
    real: "6 planes \u00d7 58 = 348 sats",
    values: { altitude_km: 560, inclination_deg: 97.6, pattern: "walker-star", planes: 6, sats_per_plane: 12, phase_offset_deg: 5.0, polar_seam_enabled: true, polar_seam_lat: 80 },
  },
  {
    label: "Kuiper (51.9\u00b0 shell)",
    desc: "630 km, 51.9\u00b0, Walker-delta. Amazon Kuiper\u2019s highest-inclination shell.",
    real: "34 planes \u00d7 34 = 1,156 sats",
    values: { altitude_km: 630, inclination_deg: 51.9, pattern: "walker-delta", planes: 6, sats_per_plane: 11, phase_offset_deg: 5.45, polar_seam_enabled: false },
  },
  {
    label: "Kuiper (42\u00b0 shell)",
    desc: "610 km, 42\u00b0, Walker-delta. Kuiper mid-inclination shell for tropical coverage.",
    real: "36 planes \u00d7 36 = 1,296 sats",
    values: { altitude_km: 610, inclination_deg: 42, pattern: "walker-delta", planes: 6, sats_per_plane: 10, phase_offset_deg: 6.0, polar_seam_enabled: false },
  },
  {
    label: "OneWeb",
    desc: "1,200 km, 87.9\u00b0, near-polar. Higher altitude gives wider per-satellite footprint.",
    real: "12 planes \u00d7 49 = 588 sats",
    values: { altitude_km: 1200, inclination_deg: 87.9, pattern: "walker-star", planes: 6, sats_per_plane: 10, phase_offset_deg: 6.0, polar_seam_enabled: true, polar_seam_lat: 75 },
  },
  {
    label: "Iridium NEXT",
    desc: "780 km, 86.4\u00b0, Walker-star. Classic polar constellation with dramatic polar seam.",
    real: "6 planes \u00d7 11 = 66 sats",
    values: { altitude_km: 780, inclination_deg: 86.4, pattern: "walker-star", planes: 6, sats_per_plane: 11, phase_offset_deg: 5.45, polar_seam_enabled: true, polar_seam_lat: 75 },
  },
  {
    label: "Telesat Lightspeed (polar)",
    desc: "1,015 km, 98.98\u00b0, sun-synchronous. Telesat\u2019s polar shell for enterprise connectivity.",
    real: "6 planes \u00d7 13 = 78 sats",
    values: { altitude_km: 1015, inclination_deg: 98.98, pattern: "walker-star", planes: 6, sats_per_plane: 13, phase_offset_deg: 4.6, polar_seam_enabled: true, polar_seam_lat: 80 },
  },
  {
    label: "SDA Transport (Tranche 1)",
    desc: "~1,000 km, ~80\u00b0, near-polar. PWSA transport layer for military mesh networking.",
    real: "6 planes \u00d7 21 = 126 sats",
    values: { altitude_km: 1000, inclination_deg: 80, pattern: "walker-star", planes: 6, sats_per_plane: 15, phase_offset_deg: 4.0, polar_seam_enabled: true, polar_seam_lat: 70 },
  },
  {
    label: "Globalstar",
    desc: "1,414 km, 52\u00b0, Walker-delta. Inclined LEO, bent-pipe architecture.",
    real: "8 planes \u00d7 6 = 48 sats",
    values: { altitude_km: 1414, inclination_deg: 52, pattern: "walker-delta", planes: 8, sats_per_plane: 6, phase_offset_deg: 7.5, polar_seam_enabled: false },
  },
];

const DEFAULTS: CustomConstellationState = {
  altitude_km: 550,
  inclination_deg: 53,
  pattern: "walker-delta",
  planes: 4,
  sats_per_plane: 11,
  raan_spacing_deg: 90,
  phase_offset_deg: 8.2,
  polar_seam_enabled: false,
  polar_seam_lat: 70,
};

/** Convert geometry values to a ConstellationPreset with inline constellation dict. */
function geometryToPreset(label: string, desc: string, _real: string, values: Partial<CustomConstellationState>): ConstellationPreset {
  const v = { ...DEFAULTS, ...values };
  if (values.planes) {
    v.raan_spacing_deg = Math.round((360 / values.planes) * 100) / 100;
  }
  const total = v.planes * v.sats_per_plane;
  const name = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-$/, "");
  return {
    name,
    description: desc,
    satellite_count: total,
    mode: "parametric",
    constellation: JSON.stringify({
      mode: "parametric",
      name,
      satellite_type: "generic-4isl",
      orbit: {
        altitude_km: v.altitude_km,
        inclination_deg: v.inclination_deg,
        pattern: v.pattern,
      },
      planes: {
        count: v.planes,
        sats_per_plane: v.sats_per_plane,
        raan_spacing_deg: v.raan_spacing_deg,
        phase_offset_deg: v.phase_offset_deg,
      },
      ...(v.polar_seam_enabled ? {
        polar_seam: { enabled: true, latitude_threshold_deg: v.polar_seam_lat },
      } : {}),
    }),
    ground_stations: "",
  };
}

/** Pre-built ConstellationPreset cards from real constellation geometries. */
const REAL_WORLD_PRESETS: (ConstellationPreset & { real: string })[] = GEOMETRY_PRESETS.map(
  (p) => ({ ...geometryToPreset(p.label, p.desc, p.real, p.values), real: p.real }),
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
        next.raan_spacing_deg = Math.round((360 / value) * 100) / 100;
      }
      return next;
    });
    setFormError(null);
  };

  const applyPreset = (label: string, values: Partial<CustomConstellationState>) => {
    setForm((prev) => {
      const next = { ...prev, ...values };
      if (values.planes) {
        next.raan_spacing_deg = Math.round((360 / values.planes) * 100) / 100;
      }
      return next;
    });
    setFormError(null);
    setSelectedTemplate(label);
  };

  const handleSubmit = () => {
    if (form.altitude_km < 160) { setFormError("Altitude must be at least 160 km"); return; }
    if (form.altitude_km > 40000) { setFormError("Altitude must be under 40,000 km"); return; }
    if (form.inclination_deg < 0 || form.inclination_deg > 180) { setFormError("Inclination must be 0\u2013180\u00b0"); return; }
    if (form.planes < 1 || form.planes > 20) { setFormError("Planes must be 1\u201320"); return; }
    if (form.sats_per_plane < 1 || form.sats_per_plane > 50) { setFormError("Satellites per plane must be 1\u201350"); return; }

    const total = form.planes * form.sats_per_plane;
    const name = `custom-${form.planes}x${form.sats_per_plane}-${form.altitude_km}km`;

    const preset: ConstellationPreset = {
      name,
      description: `${form.planes} planes \u00d7 ${form.sats_per_plane} sats, ${form.altitude_km} km, ${form.inclination_deg}\u00b0 ${form.pattern}`,
      satellite_count: total,
      mode: "parametric",
      constellation: JSON.stringify({
        mode: "parametric",
        name,
        orbit: {
          altitude_km: form.altitude_km,
          inclination_deg: form.inclination_deg,
          pattern: form.pattern,
        },
        planes: {
          count: form.planes,
          sats_per_plane: form.sats_per_plane,
          raan_spacing_deg: form.raan_spacing_deg,
          phase_offset_deg: form.phase_offset_deg,
        },
        ...(form.polar_seam_enabled ? {
          polar_seam: {
            enabled: true,
            latitude_threshold_deg: form.polar_seam_lat,
          },
        } : {}),
      }),
      ground_stations: "",
    };
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
            <div className="wizard-card-real">Real: {p.real}</div>
          </button>
        ))}
      </div>
      <h3 className="wizard-section-title">Orbital parameters</h3>

      <div className="wizard-custom-field">
        <label>Altitude (km) <Help text={CONSTELLATION_HELP.altitude_km} /></label>
        <input type="number" min={160} max={40000} value={form.altitude_km}
          onChange={(e) => set("altitude_km", parseFloat(e.target.value) || 550)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>{"Inclination (\u00b0)"} <Help text={CONSTELLATION_HELP.inclination_deg} /></label>
        <input type="number" min={0} max={180} step={0.1} value={form.inclination_deg}
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
        <input type="number" min={1} max={20} value={form.planes}
          onChange={(e) => set("planes", parseInt(e.target.value) || 4)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>Satellites per Plane <Help text={CONSTELLATION_HELP.sats_per_plane} /></label>
        <input type="number" min={1} max={50} value={form.sats_per_plane}
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
        <input type="number" min={0.1} max={360} step={0.1} value={form.raan_spacing_deg}
          onChange={(e) => set("raan_spacing_deg", parseFloat(e.target.value) || 90)}
          className="wizard-select" />
      </div>
      <div className="wizard-custom-field">
        <label>{"Phase Offset (\u00b0)"} <Help text={CONSTELLATION_HELP.phase_offset_deg} /></label>
        <input type="number" min={0} max={360} step={0.1} value={form.phase_offset_deg}
          onChange={(e) => set("phase_offset_deg", parseFloat(e.target.value) || 0)}
          className="wizard-select" />
      </div>
      {form.pattern === "walker-star" && (
        <>
          <div className="wizard-custom-field">
            <label>
              <input type="checkbox" checked={form.polar_seam_enabled}
                onChange={(e) => set("polar_seam_enabled", e.target.checked)} />
              {" "}Enable Polar Seam Cutoff <Help text={CONSTELLATION_HELP.polar_seam} />
            </label>
          </div>
          {form.polar_seam_enabled && (
            <div className="wizard-custom-field">
              <label>{"Seam Latitude Threshold (\u00b0)"}</label>
              <input type="number" min={0} max={90} value={form.polar_seam_lat}
                onChange={(e) => set("polar_seam_lat", parseFloat(e.target.value) || 70)}
                className="wizard-select" />
            </div>
          )}
        </>
      )}

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
  fallbackSessions: SessionInfo[];
  deploying: boolean;
  onFallbackDeploy: (id: string) => void;
}

export function ConstellationPanel({
  presets,
  selected,
  onSelect,
  fallbackSessions,
  deploying,
  onFallbackDeploy,
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
      <div className="wizard-loading">
        {fallbackSessions.length > 0 ? (
          <>
            <p className="catalog-fallback-warning">
              Could not load wizard presets. Showing sessions from VS-API.
            </p>
            <div className="catalog-grid">
              {fallbackSessions.map((s) => (
                <div key={s.file} className={`catalog-card ${s.active ? "catalog-card--active" : ""}`}>
                  <div className="catalog-card-header"><h3>{s.name}</h3></div>
                  <div className="catalog-card-stats">
                    <span>{s.constellation}</span>
                    <span>{s.routing_stack}</span>
                  </div>
                  <button
                    className={`catalog-deploy-btn ${s.active ? "catalog-deploy-btn--running" : deploying ? "catalog-deploy-btn--deploying" : ""}`}
                    onClick={() => onFallbackDeploy(s.file.replace("configs/sessions/", "").replace(".yaml", ""))}
                    disabled={s.active || deploying}
                  >
                    {s.active ? "Running" : deploying ? "Deploying..." : "Deploy"}
                  </button>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p>Loading presets...</p>
        )}
      </div>
    );
  }

  // Merge library and real-world presets, sort alphabetically
  const allPresets = [
    ...presets.map((p) => ({ ...p, real: undefined as string | undefined })),
    ...REAL_WORLD_PRESETS,
  ].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="wizard-grid">
      {allPresets.map((p) => (
        <button
          key={p.name}
          className={`wizard-card ${selected?.name === p.name ? "wizard-card--selected" : ""}`}
          onClick={() => onSelect(p)}
        >
          <div className="wizard-card-title">{p.name}</div>
          <div className="wizard-card-stat">{p.satellite_count} satellites</div>
          <div className="wizard-card-desc">{p.description}</div>
          {p.real && <div className="wizard-card-real">Real: {p.real}</div>}
        </button>
      ))}
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
