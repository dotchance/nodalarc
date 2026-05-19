// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Review & Deploy panel — session summary, YAML preview, deploy/download.
 *
 * Session summary, YAML preview, deploy, and download controls.
 */

import type { WizardRuntimeState } from "./wizardTypes";
import { ORBIT_MODEL_OPTIONS } from "./orbitModels";

interface ReviewPanelProps {
  state: WizardRuntimeState;
  generatedYaml: string | null;
  generating: boolean;
  deploying: boolean;
  onBack: () => void;
  onDeploy: () => void;
  onDownload: () => void;
  onReset: () => void;
}

export function ReviewPanel({
  state,
  generatedYaml,
  generating,
  deploying,
  onBack,
  onDeploy,
  onDownload,
  onReset,
}: ReviewPanelProps) {
  const orbitModelLabel =
    ORBIT_MODEL_OPTIONS.find((o) => o.id === state.orbitPropagator)?.label ?? state.orbitPropagator;

  return (
    <div className="wizard-panel">
      <h2 className="wizard-panel-title">Review &amp; Deploy</h2>
      <div className="wizard-review">
        <div className="wizard-review-row">
          <span className="wizard-review-label">Satellite Type</span>
          <span className="wizard-review-value">{state.satelliteType?.name ?? "-"}</span>
        </div>
        <div className="wizard-review-row">
          <span className="wizard-review-label">Ground Stations</span>
          <span className="wizard-review-value">{state.groundStationSet?.name ?? "-"}</span>
        </div>
        <div className="wizard-review-row">
          <span className="wizard-review-label">Constellation</span>
          <span className="wizard-review-value">{state.constellation?.name ?? "-"}</span>
        </div>
        <div className="wizard-review-row">
          <span className="wizard-review-label">Satellites</span>
          <span className="wizard-review-value">{state.constellation?.satellite_count ?? "-"}</span>
        </div>
        <div className="wizard-review-row">
          <span className="wizard-review-label">Orbit Model</span>
          <span className="wizard-review-value">{orbitModelLabel}</span>
        </div>
        <div className="wizard-review-row">
          <span className="wizard-review-label">Protocol</span>
          <span className="wizard-review-value">{state.protocol ?? "-"}</span>
        </div>
        {state.extensions.length > 0 && (
          <div className="wizard-review-row">
            <span className="wizard-review-label">Extensions</span>
            <span className="wizard-review-value">{state.extensions.join(", ")}</span>
          </div>
        )}
        {state.protocol !== "nodalpath" && (
          <div className="wizard-review-row">
            <span className="wizard-review-label">Area Strategy</span>
            <span className="wizard-review-value">{state.areaStrategy}</span>
          </div>
        )}
      </div>

      {generatedYaml && (
        <details className="wizard-yaml-preview">
          <summary>Session YAML</summary>
          <pre>{generatedYaml}</pre>
        </details>
      )}

      <div className="wizard-actions">
        <button className="wizard-nav-btn" onClick={onBack}>Back</button>
        {generatedYaml && (
          <button className="wizard-nav-btn" onClick={onDownload}>
            Download YAML
          </button>
        )}
        <button
          className="wizard-nav-btn wizard-nav-btn--primary"
          onClick={onDeploy}
          disabled={generating || deploying}
        >
          {generating ? "Generating..." : deploying ? "Deploying..." : generatedYaml ? "Deploy" : "Generate & Review"}
        </button>
      </div>

      <div className="wizard-nav">
        <button className="wizard-nav-btn wizard-nav-btn--reset" onClick={onReset}>
          Start Over
        </button>
      </div>
    </div>
  );
}
