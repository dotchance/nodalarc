/** 4-step session wizard — replaces the flat card catalog. */

import { useState, useCallback, useRef } from "react";
import { useWizard } from "../hooks/useWizard";
import type { Protocol, WizardStep, SatelliteTypePreset, AvailableStation } from "./wizardTypes";
import type { SessionInfo } from "../types";

// --- Custom Satellite Type Builder ---

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

// --- Custom Ground Station Set Builder ---

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

interface SessionWizardProps {
  onDeployStarted: () => void;
  onClose: (() => void) | undefined;
  deploying: boolean;
  fallbackSessions: SessionInfo[];
  onFallbackDeploy: (id: string) => void;
}

const STEP_LABELS: Record<WizardStep, string> = {
  "satellite-type": "Satellite",
  "ground-stations": "Ground Stations",
  constellation: "Constellation",
  protocol: "Protocol",
  extensions: "Options",
  review: "Review",
};

const STEP_ORDER: WizardStep[] = ["satellite-type", "ground-stations", "constellation", "protocol", "extensions", "review"];

const PROTOCOL_INFO: Record<string, { label: string; description: string; disabled?: boolean; disabledReason?: string }> = {
  ospf: { label: "OSPF", description: "Open Shortest Path First. Distributed link-state routing." },
  isis: { label: "IS-IS", description: "Intermediate System to Intermediate System. Native CLNS routing." },
  bgp: { label: "BGP", description: "Border Gateway Protocol.", disabled: true, disabledReason: "Coming Soon" },
  nodalpath: { label: "NodalPath", description: "Centralized MPLS path computation (NEBULA model). No FRR routing daemon." },
};

const EXTENSION_INFO: Record<string, { label: string; description: string }> = {
  te: { label: "Traffic Engineering", description: "MPLS-TE extensions. Advertises bandwidth and delay." },
  mpls: { label: "MPLS / LDP", description: "Label Distribution Protocol for MPLS forwarding plane." },
  sr: { label: "Segment Routing", description: "Source-routed MPLS with SRGB label blocks." },
};

export function SessionWizard({
  onDeployStarted,
  onClose,
  deploying,
  fallbackSessions,
  onFallbackDeploy,
}: SessionWizardProps) {
  const wizard = useWizard();
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [showCustomSat, setShowCustomSat] = useState(false);
  const [showCustomGs, setShowCustomGs] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDeploy = useCallback(async () => {
    if (!wizard.generatedYaml) {
      await wizard.generate();
      return;
    }
    const ok = await wizard.deploy(wizard.generatedYaml);
    if (ok) {
      onDeployStarted();
    }
  }, [wizard, onDeployStarted]);

  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError(null);
    try {
      const text = await file.text();
      const ok = await wizard.deploy(text);
      if (ok) onDeployStarted();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [wizard, onDeployStarted]);

  const handleDownload = useCallback(() => {
    if (!wizard.generatedYaml) return;
    const blob = new Blob([wizard.generatedYaml], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const name = wizard.state.constellation?.name ?? "session";
    const proto = wizard.state.protocol ?? "unknown";
    a.href = url;
    a.download = `${name}-${proto}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  }, [wizard]);

  const stepIdx = STEP_ORDER.indexOf(wizard.state.step);

  return (
    <div className="catalog-overlay">
      <h1 className="catalog-header">NODAL ARC</h1>
      <p className="catalog-subtitle">Orbital Network Emulation Lab</p>

      {/* Step indicator */}
      <div className="wizard-steps">
        {STEP_ORDER.map((step, i) => (
          <button
            key={step}
            className={`wizard-step-pill ${wizard.state.step === step ? "wizard-step-pill--active" : ""} ${i < stepIdx ? "wizard-step-pill--done" : ""}`}
            onClick={() => i < stepIdx && wizard.goToStep(step)}
            disabled={i > stepIdx}
          >
            <span className="wizard-step-num">{i + 1}</span>
            {STEP_LABELS[step]}
          </button>
        ))}
      </div>

      {/* Upload shortcut — always visible, bypasses wizard */}
      <div className="wizard-upload">
        <span>Or deploy from a session YAML file:</span>
        <input ref={fileInputRef} type="file" accept=".yaml,.yml" onChange={handleUpload} />
      </div>
      {uploadError && <div className="wizard-error">{uploadError}</div>}

      {wizard.error && <div className="wizard-error">{wizard.error}</div>}

      {/* Step 1: Satellite Type */}
      {wizard.state.step === "satellite-type" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Select Satellite Type</h2>
          {showCustomSat ? (
            <CustomSatelliteForm
              onSubmit={(preset) => { setShowCustomSat(false); wizard.selectSatelliteType(preset); }}
              onCancel={() => setShowCustomSat(false)}
            />
          ) : wizard.satelliteTypes.length === 0 ? (
            <div className="wizard-loading"><p>Loading satellite types...</p></div>
          ) : (
            <div className="wizard-grid">
              {wizard.satelliteTypes.map((st) => (
                <button
                  key={st.name}
                  className={`wizard-card ${wizard.state.satelliteType?.name === st.name ? "wizard-card--selected" : ""}`}
                  onClick={() => wizard.selectSatelliteType(st)}
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
                onClick={() => setShowCustomSat(true)}
              >
                <div className="wizard-card-title">Custom</div>
                <div className="wizard-card-desc">
                  Define custom ISL and ground terminal groups with full control over type, count, range, bandwidth, tracking rate, and field of regard.
                </div>
              </button>
            </div>
          )}
        </div>
      )}

      {/* Step 2: Ground Station Set */}
      {wizard.state.step === "ground-stations" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Select Ground Station Set</h2>
          {showCustomGs ? (
            <CustomGroundStationsForm
              stations={wizard.availableStations}
              onSubmit={(names) => { setShowCustomGs(false); wizard.selectCustomGroundStations(names); }}
              onCancel={() => setShowCustomGs(false)}
            />
          ) : wizard.groundStationSets.length === 0 ? (
            <div className="wizard-loading"><p>Loading ground station sets...</p></div>
          ) : (
            <div className="wizard-grid">
              {wizard.groundStationSets.map((gs) => (
                <button
                  key={gs.name}
                  className={`wizard-card ${wizard.state.groundStationSet?.name === gs.name ? "wizard-card--selected" : ""}`}
                  onClick={() => wizard.selectGroundStationSet(gs)}
                >
                  <div className="wizard-card-title">{gs.name}</div>
                  <div className="wizard-card-stat">{gs.stations.length} stations</div>
                  <div className="wizard-card-desc">{gs.description}</div>
                  <div className="wizard-station-list">{gs.stations.join(", ")}</div>
                </button>
              ))}
              <button
                className="wizard-card wizard-card--custom"
                onClick={() => setShowCustomGs(true)}
              >
                <div className="wizard-card-title">Custom</div>
                <div className="wizard-card-desc">
                  Pick individual ground stations from the available {wizard.availableStations.length} locations to build a custom set.
                </div>
              </button>
            </div>
          )}
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
          </div>
        </div>
      )}

      {/* Step 3: Constellation */}
      {wizard.state.step === "constellation" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Select Constellation</h2>
          {wizard.presets.length === 0 ? (
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
          ) : (
            <div className="wizard-grid">
              {wizard.presets.map((p) => (
                <button
                  key={p.name}
                  className={`wizard-card ${wizard.state.constellation?.name === p.name ? "wizard-card--selected" : ""}`}
                  onClick={() => wizard.selectConstellation(p)}
                >
                  <div className="wizard-card-title">{p.name}</div>
                  <div className="wizard-card-stat">{p.satellite_count} satellites</div>
                  <div className="wizard-card-desc">{p.description}</div>
                </button>
              ))}
            </div>
          )}
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
          </div>
        </div>
      )}

      {/* Step 4: Protocol */}
      {wizard.state.step === "protocol" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Select Routing Protocol</h2>
          <div className="wizard-protocol-list">
            {Object.entries(PROTOCOL_INFO).map(([key, info]) => (
              <button
                key={key}
                className={`wizard-protocol-btn ${wizard.state.protocol === key ? "wizard-protocol-btn--selected" : ""} ${info.disabled ? "wizard-protocol-btn--disabled" : ""}`}
                onClick={() => !info.disabled && wizard.selectProtocol(key as Protocol)}
                disabled={info.disabled}
                title={info.disabled ? info.disabledReason : undefined}
              >
                <div className="wizard-protocol-label">
                  {info.label}
                  {info.disabled && <span className="wizard-badge-soon">{info.disabledReason}</span>}
                </div>
                <div className="wizard-protocol-desc">{info.description}</div>
              </button>
            ))}
          </div>
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
          </div>
        </div>
      )}

      {/* Step 5: Extensions + Area Strategy */}
      {wizard.state.step === "extensions" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Extensions &amp; Area Strategy</h2>
          <div className="wizard-section">
            <h3 className="wizard-section-title">Extensions</h3>
            <div className="wizard-ext-list">
              {Object.entries(EXTENSION_INFO).map(([key, info]) => {
                const allowed = wizard.isExtensionAllowed(key);
                const enabled = wizard.isExtensionEnabled(key);
                const checked = wizard.state.extensions.includes(key);
                return (
                  <label
                    key={key}
                    className={`wizard-ext-item ${!allowed ? "wizard-ext-item--unavailable" : !enabled ? "wizard-ext-item--disabled" : ""}`}
                    title={!allowed ? `Not available for ${wizard.state.protocol}` : !enabled ? "Requires missing dependency" : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => wizard.toggleExtension(key)}
                      disabled={!allowed || (!enabled && !checked)}
                    />
                    <span className="wizard-ext-label">{info.label}</span>
                    <span className="wizard-ext-desc">{info.description}</span>
                  </label>
                );
              })}
            </div>
          </div>
          <div className="wizard-section">
            <h3 className="wizard-section-title">Area Strategy</h3>
            <select
              className="wizard-select"
              value={wizard.state.areaStrategy}
              onChange={(e) => wizard.setAreaStrategy(e.target.value)}
            >
              {wizard.rules?.area_strategies.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
            <button className="wizard-nav-btn wizard-nav-btn--primary" onClick={wizard.goToReview}>
              Review
            </button>
          </div>
        </div>
      )}

      {/* Step 6: Review */}
      {wizard.state.step === "review" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Review &amp; Deploy</h2>
          <div className="wizard-review">
            <div className="wizard-review-row">
              <span className="wizard-review-label">Satellite Type</span>
              <span className="wizard-review-value">{wizard.state.satelliteType?.name ?? "-"}</span>
            </div>
            <div className="wizard-review-row">
              <span className="wizard-review-label">Ground Stations</span>
              <span className="wizard-review-value">{wizard.state.groundStationSet?.name ?? "-"}</span>
            </div>
            <div className="wizard-review-row">
              <span className="wizard-review-label">Constellation</span>
              <span className="wizard-review-value">{wizard.state.constellation?.name ?? "-"}</span>
            </div>
            <div className="wizard-review-row">
              <span className="wizard-review-label">Satellites</span>
              <span className="wizard-review-value">{wizard.state.constellation?.satellite_count ?? "-"}</span>
            </div>
            <div className="wizard-review-row">
              <span className="wizard-review-label">Protocol</span>
              <span className="wizard-review-value">{wizard.state.protocol ?? "-"}</span>
            </div>
            {wizard.state.extensions.length > 0 && (
              <div className="wizard-review-row">
                <span className="wizard-review-label">Extensions</span>
                <span className="wizard-review-value">{wizard.state.extensions.join(", ")}</span>
              </div>
            )}
            {wizard.state.protocol !== "nodalpath" && (
              <div className="wizard-review-row">
                <span className="wizard-review-label">Area Strategy</span>
                <span className="wizard-review-value">{wizard.state.areaStrategy}</span>
              </div>
            )}
          </div>

          {wizard.generatedYaml && (
            <details className="wizard-yaml-preview">
              <summary>Session YAML</summary>
              <pre>{wizard.generatedYaml}</pre>
            </details>
          )}

          <div className="wizard-actions">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
            {wizard.generatedYaml && (
              <button className="wizard-nav-btn" onClick={handleDownload}>
                Download YAML
              </button>
            )}
            <button
              className="wizard-nav-btn wizard-nav-btn--primary"
              onClick={handleDeploy}
              disabled={wizard.generating || wizard.deploying || deploying}
            >
              {wizard.generating ? "Generating..." : wizard.deploying || deploying ? "Deploying..." : wizard.generatedYaml ? "Deploy" : "Generate & Review"}
            </button>
          </div>

          <div className="wizard-nav">
            <button className="wizard-nav-btn wizard-nav-btn--reset" onClick={wizard.reset}>
              Start Over
            </button>
          </div>
        </div>
      )}

      <div className="catalog-footer">
        {onClose && <span style={{ cursor: "pointer" }} onClick={onClose}>Press Esc to close</span>}
      </div>
    </div>
  );
}
