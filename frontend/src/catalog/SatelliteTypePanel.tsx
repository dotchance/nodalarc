// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Elastic License 2.0 (ELv2). See LICENSE file.
/** Satellite type selection panel — library list + custom builder.
 *
 * Extracted from SessionWizard.tsx lines 38-441 with zero behavior change.
 * The CustomSatelliteForm and library grid render identically.
 */

import { useState } from "react";
import type { SatelliteTypePreset } from "./wizardTypes";

// --- Form types (internal to this panel) ---

interface IslTerminalForm {
  type: string;
  band: string;
  count: number;
  role: string;
  max_range_km: number;
  bandwidth_mbps: number;
  max_tracking_rate_deg_s: number;
  field_of_regard_deg: number;
}

interface GroundTerminalForm {
  type: string;
  band: string;
  count: number;
  bandwidth_mbps: number;
}

const DEFAULT_ISL: IslTerminalForm = {
  type: "optical", band: "", count: 2, role: "",
  max_range_km: 5000, bandwidth_mbps: 100,
  max_tracking_rate_deg_s: 3.0, field_of_regard_deg: 140,
};

const DEFAULT_GROUND: GroundTerminalForm = {
  type: "optical", band: "", count: 1, bandwidth_mbps: 1000,
};

// --- Custom satellite type builder ---

function CustomSatelliteForm({ onSubmit, onCancel }: {
  onSubmit: (preset: SatelliteTypePreset) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("custom");
  const [islTerminals, setIslTerminals] = useState<IslTerminalForm[]>([{ ...DEFAULT_ISL }]);
  const [gndTerminals, setGndTerminals] = useState<GroundTerminalForm[]>([{ ...DEFAULT_GROUND }]);
  const [formError, setFormError] = useState<string | null>(null);

  const updateIsl = (idx: number, field: string, value: string | number) => {
    setIslTerminals((prev) => prev.map((t, i) => i === idx ? { ...t, [field]: value } : t));
  };

  const updateGnd = (idx: number, field: string, value: string | number) => {
    setGndTerminals((prev) => prev.map((t, i) => i === idx ? { ...t, [field]: value } : t));
  };

  const addIsl = () => {
    const total = islTerminals.reduce((s, t) => s + t.count, 0);
    if (total >= 8) { setFormError("Max 8 ISL terminals total"); return; }
    setIslTerminals((prev) => [...prev, { ...DEFAULT_ISL }]);
    setFormError(null);
  };

  const removeIsl = (idx: number) => {
    if (islTerminals.length <= 1) return;
    setIslTerminals((prev) => prev.filter((_, i) => i !== idx));
  };

  const addGnd = () => {
    const total = gndTerminals.reduce((s, t) => s + t.count, 0);
    if (total >= 4) { setFormError("Max 4 ground terminals total"); return; }
    setGndTerminals((prev) => [...prev, { ...DEFAULT_GROUND }]);
    setFormError(null);
  };

  const removeGnd = (idx: number) => {
    if (gndTerminals.length <= 1) return;
    setGndTerminals((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = () => {
    const totalIsl = islTerminals.reduce((s, t) => s + t.count, 0);
    const totalGnd = gndTerminals.reduce((s, t) => s + t.count, 0);
    if (totalIsl < 1) { setFormError("Need at least 1 ISL terminal"); return; }
    if (totalIsl > 8) { setFormError("Max 8 ISL terminals total"); return; }
    if (totalGnd > 4) { setFormError("Max 4 ground terminals total"); return; }
    if (!name.trim()) { setFormError("Name is required"); return; }

    const preset: SatelliteTypePreset = {
      name: name.trim(),
      description: "Custom satellite type",
      isl_terminals: islTerminals.map((t) => ({
        type: t.type,
        band: t.band || undefined,
        count: t.count,
        role: t.role || undefined,
        max_range_km: t.max_range_km,
        bandwidth_mbps: t.bandwidth_mbps,
        max_tracking_rate_deg_s: t.max_tracking_rate_deg_s,
        field_of_regard_deg: t.field_of_regard_deg,
      })),
      ground_terminals: gndTerminals.map((t) => ({
        type: t.type,
        band: t.band || undefined,
        count: t.count,
        bandwidth_mbps: t.bandwidth_mbps,
      })),
    };
    onSubmit(preset);
  };

  return (
    <div className="wizard-custom-form">
      <div className="wizard-custom-field">
        <label>Name</label>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} className="wizard-select" />
      </div>

      <h3 className="wizard-section-title">ISL Terminal Groups</h3>
      {islTerminals.map((t, idx) => (
        <div key={idx} className="wizard-terminal-group">
          <div className="wizard-terminal-group-header">
            <span>ISL Group {idx + 1}</span>
            {islTerminals.length > 1 && (
              <button className="wizard-remove-btn" onClick={() => removeIsl(idx)}>Remove</button>
            )}
          </div>
          <div className="wizard-terminal-fields">
            <label>Type
              <select value={t.type} onChange={(e) => updateIsl(idx, "type", e.target.value)} className="wizard-select">
                <option value="optical">Optical</option>
                <option value="rf">RF</option>
              </select>
            </label>
            {t.type === "rf" && (
              <label>Band
                <select value={t.band} onChange={(e) => updateIsl(idx, "band", e.target.value)} className="wizard-select">
                  <option value="">-</option>
                  <option value="Ka">Ka</option>
                  <option value="Ku">Ku</option>
                  <option value="V">V</option>
                </select>
              </label>
            )}
            <label>Count
              <input type="number" min={1} max={8} value={t.count} onChange={(e) => updateIsl(idx, "count", parseInt(e.target.value) || 1)} className="wizard-select" />
            </label>
            <label>Role
              <select value={t.role} onChange={(e) => updateIsl(idx, "role", e.target.value)} className="wizard-select">
                <option value="">Pool (any)</option>
                <option value="intra-plane">Intra-plane</option>
                <option value="cross-plane">Cross-plane</option>
              </select>
            </label>
            <label>Range (km)
              <input type="number" min={100} max={10000} value={t.max_range_km} onChange={(e) => updateIsl(idx, "max_range_km", parseFloat(e.target.value) || 1000)} className="wizard-select" />
            </label>
            <label>Bandwidth (Mbps)
              <input type="number" min={1} max={100000} value={t.bandwidth_mbps} onChange={(e) => updateIsl(idx, "bandwidth_mbps", parseFloat(e.target.value) || 10)} className="wizard-select" />
            </label>
            <label>Track Rate (deg/s)
              <input type="number" min={0.1} max={20} step={0.1} value={t.max_tracking_rate_deg_s} onChange={(e) => updateIsl(idx, "max_tracking_rate_deg_s", parseFloat(e.target.value) || 1)} className="wizard-select" />
            </label>
            <label>Field of Regard (deg)
              <input type="number" min={0} max={360} value={t.field_of_regard_deg} onChange={(e) => updateIsl(idx, "field_of_regard_deg", parseFloat(e.target.value) || 90)} className="wizard-select" />
            </label>
          </div>
        </div>
      ))}
      <button className="wizard-nav-btn" onClick={addIsl}>+ Add ISL Group</button>

      <h3 className="wizard-section-title" style={{ marginTop: 16 }}>Ground Terminal Groups</h3>
      {gndTerminals.map((t, idx) => (
        <div key={idx} className="wizard-terminal-group">
          <div className="wizard-terminal-group-header">
            <span>Ground Group {idx + 1}</span>
            {gndTerminals.length > 1 && (
              <button className="wizard-remove-btn" onClick={() => removeGnd(idx)}>Remove</button>
            )}
          </div>
          <div className="wizard-terminal-fields">
            <label>Type
              <select value={t.type} onChange={(e) => updateGnd(idx, "type", e.target.value)} className="wizard-select">
                <option value="optical">Optical</option>
                <option value="rf">RF</option>
              </select>
            </label>
            {t.type === "rf" && (
              <label>Band
                <select value={t.band} onChange={(e) => updateGnd(idx, "band", e.target.value)} className="wizard-select">
                  <option value="">-</option>
                  <option value="Ka">Ka</option>
                  <option value="Ku">Ku</option>
                  <option value="V">V</option>
                </select>
              </label>
            )}
            <label>Count
              <input type="number" min={1} max={4} value={t.count} onChange={(e) => updateGnd(idx, "count", parseInt(e.target.value) || 1)} className="wizard-select" />
            </label>
            <label>Bandwidth (Mbps)
              <input type="number" min={1} max={100000} value={t.bandwidth_mbps} onChange={(e) => updateGnd(idx, "bandwidth_mbps", parseFloat(e.target.value) || 100)} className="wizard-select" />
            </label>
          </div>
        </div>
      ))}
      <button className="wizard-nav-btn" onClick={addGnd}>+ Add Ground Group</button>

      {formError && <div className="wizard-error" style={{ marginTop: 12 }}>{formError}</div>}

      <div className="wizard-nav" style={{ marginTop: 16 }}>
        <button className="wizard-nav-btn" onClick={onCancel}>Cancel</button>
        <button className="wizard-nav-btn wizard-nav-btn--primary" onClick={handleSubmit}>Use Custom Type</button>
      </div>
    </div>
  );
}

// --- Public panel component ---

interface SatelliteTypePanelProps {
  satelliteTypes: SatelliteTypePreset[];
  selected: SatelliteTypePreset | null;
  onSelect: (preset: SatelliteTypePreset) => void;
}

export function SatelliteTypePanel({ satelliteTypes, selected, onSelect }: SatelliteTypePanelProps) {
  const [showCustom, setShowCustom] = useState(false);

  if (showCustom) {
    return (
      <CustomSatelliteForm
        onSubmit={(preset) => { setShowCustom(false); onSelect(preset); }}
        onCancel={() => setShowCustom(false)}
      />
    );
  }

  if (satelliteTypes.length === 0) {
    return <div className="wizard-loading"><p>Loading satellite types...</p></div>;
  }

  return (
    <div className="wizard-grid">
      {satelliteTypes.map((st) => (
        <button
          key={st.name}
          className={`wizard-card ${selected?.name === st.name ? "wizard-card--selected" : ""}`}
          onClick={() => onSelect(st)}
        >
          <div className="wizard-card-title">{st.name}</div>
          {st.description && <div className="wizard-card-desc">{st.description}</div>}
          <div className="wizard-sat-summary">
            {st.isl_terminals.map((t, i) => (
              <div key={i} className="wizard-terminal-row">
                {t.count}x {t.type}{t.band ? ` ${t.band}` : ""} ISL{t.role ? ` (${t.role})` : ""} &mdash; {t.max_range_km} km, {t.bandwidth_mbps} Mbps, {t.max_tracking_rate_deg_s} deg/s
              </div>
            ))}
            {st.ground_terminals.map((t, i) => (
              <div key={`g${i}`} className="wizard-terminal-row">
                {t.count}x {t.type}{t.band ? ` ${t.band}` : ""} ground &mdash; {t.bandwidth_mbps} Mbps
              </div>
            ))}
          </div>
        </button>
      ))}
      <button
        className="wizard-card wizard-card--custom"
        onClick={() => setShowCustom(true)}
      >
        <div className="wizard-card-title">Custom</div>
        <div className="wizard-card-desc">
          Define custom ISL and ground terminal groups with full control over type, count, range, bandwidth, tracking rate, and field of regard.
        </div>
      </button>
    </div>
  );
}
