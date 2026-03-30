/** Session wizard — step-by-step session builder. */

import { useState, useCallback, useRef } from "react";
import { useWizard } from "../hooks/useWizard";
import type { Protocol, WizardStep } from "./wizardTypes";
import type { SessionInfo } from "../types";
import { SatelliteTypePanel } from "./SatelliteTypePanel";
import { GroundStationPanel } from "./GroundStationPanel";

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
          <SatelliteTypePanel
            satelliteTypes={wizard.satelliteTypes}
            selected={wizard.state.satelliteType}
            onSelect={wizard.selectSatelliteType}
          />
        </div>
      )}

      {/* Step 2: Ground Station Set */}
      {wizard.state.step === "ground-stations" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Select Ground Station Set</h2>
          <GroundStationPanel
            groundStationSets={wizard.groundStationSets}
            availableStations={wizard.availableStations}
            selected={wizard.state.groundStationSet}
            onSelectSet={wizard.selectGroundStationSet}
            onSelectCustom={wizard.selectCustomGroundStations}
          />
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
            {wizard.state.protocol === "ospf" && wizard.state.areaStrategy !== "flat" && (
              <div className="wizard-warning" style={{
                marginTop: 8, padding: "8px 12px", background: "rgba(200, 160, 40, 0.15)",
                border: "1px solid rgba(200, 160, 40, 0.4)", borderRadius: 4, fontSize: 12,
                color: "var(--text-dim, #aaa)", lineHeight: 1.4,
              }}>
                OSPF multi-area with dynamic constellation topologies may experience
                backbone (area 0) non-contiguity when cross-plane ISLs drop at polar
                latitudes. This can cause inter-area routing failures. IS-IS does not
                have this limitation. Use flat area strategy for reliable OSPF connectivity.
              </div>
            )}
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
