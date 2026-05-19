// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Session wizard — shell that composes extracted panels.
 *
 * Step Group A: SelectionCards (constellation, satellite type, GS, orbit model)
 * Step Group B: Protocol, Extensions, Review (linear after preview)
 */

import { useState, useCallback, useRef } from "react";
import { useWizard } from "../hooks/useWizard";
import type { WizardStep } from "./wizardTypes";
import { SelectionCards } from "./SelectionCards";
import { CoveragePreview } from "./CoveragePreview";
import { ProtocolSelection, ExtensionsPanel } from "./ProtocolPanel";
import { ReviewPanel } from "./ReviewPanel";

interface SessionWizardProps {
  onDeployStarted: () => void;
  onClose: (() => void) | undefined;
  deploying: boolean;
  systemNotice?: string;
}

const GROUP_B_STEPS: { id: WizardStep; label: string }[] = [
  { id: "protocol", label: "Protocol" },
  { id: "extensions", label: "Options" },
  { id: "review", label: "Review" },
];

export function SessionWizard({
  onDeployStarted,
  onClose,
  deploying,
  systemNotice,
}: SessionWizardProps) {
  const wizard = useWizard();
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isGroupA = wizard.state.step === "selections"
    || wizard.state.step === "satellite-type"
    || wizard.state.step === "ground-stations"
    || wizard.state.step === "constellation";

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

  const allGroupASelected =
    wizard.state.constellation !== null &&
    wizard.state.satelliteType !== null &&
    wizard.state.groundStationSet !== null;

  return (
    <div className="catalog-overlay">
      <h1 className="catalog-header">NODAL ARC</h1>
      <p className="catalog-subtitle">Orbital Network Emulation Lab</p>
      {systemNotice && <div className="wizard-warning">{systemNotice}</div>}

      {/* Step indicator — simplified for group model */}
      <div className="wizard-steps">
        <button
          className={`wizard-step-pill ${isGroupA ? "wizard-step-pill--active" : "wizard-step-pill--done"}`}
          onClick={() => isGroupA ? undefined : wizard.goToStep("selections" as WizardStep)}
        >
          <span className="wizard-step-num">1</span>
          Configuration
        </button>
        {GROUP_B_STEPS.map(({ id, label }, i) => {
          const isActive = wizard.state.step === id;
          const groupBIdx = GROUP_B_STEPS.findIndex((s) => s.id === wizard.state.step);
          const isDone = !isGroupA && groupBIdx > i;
          return (
            <button
              key={id}
              className={`wizard-step-pill ${isActive ? "wizard-step-pill--active" : ""} ${isDone ? "wizard-step-pill--done" : ""}`}
              onClick={() => isDone && wizard.goToStep(id)}
              disabled={isGroupA || (!isActive && !isDone)}
            >
              <span className="wizard-step-num">{i + 2}</span>
              {label}
            </button>
          );
        })}
      </div>

      {/* Upload shortcut — always visible, bypasses wizard */}
      <div className="wizard-upload">
        <span>Or deploy from a session YAML file:</span>
        <input ref={fileInputRef} type="file" accept=".yaml,.yml" onChange={handleUpload} />
      </div>
      {uploadError && <div className="wizard-error">{uploadError}</div>}
      {wizard.error && <div className="wizard-error">{wizard.error}</div>}

      {/* Step Group A: Selection Cards */}
      {isGroupA && !wizard.coveragePreview && (
        <SelectionCards
          presets={wizard.presets}
          satelliteTypes={wizard.satelliteTypes}
          groundStationSets={wizard.groundStationSets}
          availableStations={wizard.availableStations}
          constellation={wizard.state.constellation}
          satelliteType={wizard.state.satelliteType}
          groundStationSet={wizard.state.groundStationSet}
          orbitPropagator={wizard.state.orbitPropagator}
          onSelectConstellation={wizard.selectConstellation}
          onSelectSatelliteType={wizard.selectSatelliteType}
          onSelectGroundStationSet={wizard.selectGroundStationSet}
          onSelectCustomGroundStations={wizard.selectCustomGroundStations}
          onSelectOrbitPropagator={wizard.selectOrbitPropagator}
          onPreview={wizard.previewCoverage}
          onContinueWithoutPreview={wizardToProtocol}
          canPreview={allGroupASelected}
          previewing={wizard.previewing}
        />
      )}

      {/* Coverage Preview Results */}
      {isGroupA && wizard.coveragePreview && (
        <CoveragePreview
          result={wizard.coveragePreview}
          onContinue={wizardToProtocol}
          onBack={wizard.clearPreview}
        />
      )}

      {/* Step Group B: Protocol */}
      {wizard.state.step === "protocol" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Select Routing Protocol</h2>
          <ProtocolSelection
            selected={wizard.state.protocol}
            onSelect={wizard.selectProtocol}
          />
          <div className="wizard-nav">
            <button className="wizard-nav-btn" onClick={() => wizard.goToStep("selections" as WizardStep)}>
              Back to Configuration
            </button>
          </div>
        </div>
      )}

      {/* Step Group B: Extensions + Area Strategy */}
      {wizard.state.step === "extensions" && (
        <div className="wizard-panel">
          <h2 className="wizard-panel-title">Extensions &amp; Area Strategy</h2>
          <ExtensionsPanel
            protocol={wizard.state.protocol}
            extensions={wizard.state.extensions}
            areaStrategy={wizard.state.areaStrategy}
            rules={wizard.rules}
            routingTimers={wizard.state.routingTimers}
            onToggleExtension={wizard.toggleExtension}
            onSetAreaStrategy={wizard.setAreaStrategy}
            onUpdateTimers={wizard.updateTimers}
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

      {/* Step Group B: Review & Deploy */}
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
