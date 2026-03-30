/** Session wizard — shell that composes extracted panels.
 *
 * Each panel is a focused component under 300 lines.
 * This file handles: step indicator, upload shortcut, error display,
 * step routing, and deploy/download actions.
 */

import { useState, useCallback, useRef } from "react";
import { useWizard } from "../hooks/useWizard";
import type { Protocol, WizardStep } from "./wizardTypes";
import type { SessionInfo } from "../types";
import { SatelliteTypePanel } from "./SatelliteTypePanel";
import { GroundStationPanel } from "./GroundStationPanel";
import { ConstellationPanel } from "./ConstellationPanel";
import { ProtocolSelection, ExtensionsPanel } from "./ProtocolPanel";
import { ReviewPanel } from "./ReviewPanel";

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

const STEP_ORDER: WizardStep[] = [
  "satellite-type",
  "ground-stations",
  "constellation",
  "protocol",
  "extensions",
  "review",
];

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

  const handleUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
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
    },
    [wizard, onDeployStarted],
  );

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
          <ConstellationPanel
            presets={wizard.presets}
            selected={wizard.state.constellation}
            onSelect={wizard.selectConstellation}
            fallbackSessions={fallbackSessions}
            deploying={deploying}
            onFallbackDeploy={onFallbackDeploy}
          />
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
          </div>
        </div>
      )}

      {/* Step 4: Protocol */}
      {wizard.state.step === "protocol" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Select Routing Protocol</h2>
          <ProtocolSelection
            selected={wizard.state.protocol}
            onSelect={wizard.selectProtocol}
          />
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
          </div>
        </div>
      )}

      {/* Step 5: Extensions + Area Strategy */}
      {wizard.state.step === "extensions" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Extensions &amp; Area Strategy</h2>
          <ExtensionsPanel
            protocol={wizard.state.protocol}
            extensions={wizard.state.extensions}
            areaStrategy={wizard.state.areaStrategy}
            rules={wizard.rules}
            onToggleExtension={wizard.toggleExtension}
            onSetAreaStrategy={wizard.setAreaStrategy}
            isExtensionAllowed={wizard.isExtensionAllowed}
            isExtensionEnabled={wizard.isExtensionEnabled}
          />
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={wizard.goBack}>Back</button>
            <button className="wizard-nav-btn wizard-nav-btn--primary" onClick={wizard.goToReview}>
              Review
            </button>
          </div>
        </div>
      )}

      {/* Step 6: Review & Deploy */}
      {wizard.state.step === "review" && (
        <ReviewPanel
          state={wizard.state}
          generatedYaml={wizard.generatedYaml}
          generating={wizard.generating}
          deploying={wizard.deploying || deploying}
          onBack={wizard.goBack}
          onDeploy={handleDeploy}
          onDownload={handleDownload}
          onReset={wizard.reset}
        />
      )}

      <div className="catalog-footer">
        {onClose && (
          <span style={{ cursor: "pointer" }} onClick={onClose}>
            Press Esc to close
          </span>
        )}
      </div>
    </div>
  );
}
