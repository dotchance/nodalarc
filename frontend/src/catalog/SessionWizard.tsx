// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Session launcher — two working surfaces over one overlay:
 *
 *  Launch: shipped catalog sessions as dense rows, deployed as-is via the
 *          session switch, plus raw-YAML file deploy.
 *  Build:  the wizard flow (Configuration → Protocol → Options → Review)
 *          compiling, saving, and deploying through Builder authority.
 *
 * The two entry paths are separated structurally instead of stacked in one
 * column; the state machine, gates, and wire payloads are unchanged.
 */

import { useState, useCallback, useRef } from "react";
import { useWizard } from "../hooks/useWizard";
import type { WizardStep } from "./wizardTypes";
import type { SessionInfo } from "../types";
import type { CatalogSessionSourceId } from "../builder/generated/builderApi";
import { REST_URL, authHeaders } from "../config";
import { apiErrorMessage, apiErrorFromException } from "../ui/apiError";
import { downloadBlob } from "../ui/downloadBlob";
import { Badge } from "../ui/Badge";
import { Button, IconButton } from "../ui/Button";
import { SelectionCards } from "./SelectionCards";
import { CoveragePreview } from "./CoveragePreview";
import { ProtocolSelection, ExtensionsPanel } from "./ProtocolPanel";
import { ReviewPanel } from "./ReviewPanel";

interface SessionWizardProps {
  onDeployStarted: () => void;
  onClose: (() => void) | undefined;
  deploying: boolean;
  systemNotice?: string;
  /** Shipped catalog sessions, deployable as-is via session switch. */
  sessions: SessionInfo[];
  onLaunchSession: (session: SessionInfo) => void;
  /** Feature-gated session-builder entry: navigates to the builder view.
   *  Day-0 authoring needs no deployed session, so this bypasses the
   *  hasEverDeployed close gate — it goes somewhere, not to nothing. */
  onOpenBuilder?: () => void;
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
  sessions,
  onLaunchSession,
  onOpenBuilder,
}: SessionWizardProps) {
  const wizard = useWizard();
  const [view, setView] = useState<"launch" | "build">(sessions.length > 0 ? "launch" : "build");
  // Any listed session's stored YAML is downloadable, including the running one.
  const [downloadingFile, setDownloadingFile] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const downloadSessionYaml = useCallback(async (source: CatalogSessionSourceId, name: string) => {
    setDownloadError(null);
    setDownloadingFile(source.session_ref);
    try {
      const response = await fetch(
        `${REST_URL}/api/v1/sessions/yaml?session_ref=${encodeURIComponent(source.session_ref)}`,
        { headers: authHeaders() },
      );
      if (!response.ok) throw new Error(await apiErrorMessage(response));
      downloadBlob(await response.text(), `${name}.yaml`);
    } catch (e) {
      setDownloadError(apiErrorFromException(e));
    } finally {
      setDownloadingFile(null);
    }
  }, []);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isGroupA = wizard.state.step === "selections"
    || wizard.state.step === "ground-stations"
    || wizard.state.step === "constellation";

  const handleDeploy = useCallback(async () => {
    if (!wizard.generatedYaml) {
      await wizard.generate();
      return;
    }
    const ok = await wizard.deploy();
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
        const ok = await wizard.deployUploadedYaml(text);
        if (ok) onDeployStarted();
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : "Upload failed");
      }
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [wizard, onDeployStarted],
  );

  const handleDownload = useCallback(async () => {
    const portableClosure = await wizard.exportClosure();
    if (!portableClosure) return;
    const name = wizard.state.constellation?.name ?? "session";
    const proto = wizard.state.protocol ?? "unknown";
    downloadBlob(
      JSON.stringify(portableClosure, null, 2),
      `${name}-${proto}.nodalarc-session-closure.json`,
      "application/json",
    );
  }, [wizard]);

  const allGroupASelected =
    wizard.state.constellation !== null &&
    wizard.state.groundStationSet !== null;

  return (
    <div className="launcher-overlay">
      <div className="launcher-shell">
        <header className="launcher-head">
          <h1>Sessions</h1>
          {onClose && <IconButton icon="x" label="Close (Esc)" onClick={onClose} />}
        </header>
        {systemNotice && <div className="wizard-warning">{systemNotice}</div>}

        <div className="launcher-body">
          <nav className="launcher-rail" aria-label="Session source">
            <button
              className={`launcher-rail-btn${view === "launch" ? " launcher-rail-btn--active" : ""}`}
              onClick={() => setView("launch")}
            >
              Launch
            </button>
            <button
              className={`launcher-rail-btn${view === "build" ? " launcher-rail-btn--active" : ""}`}
              onClick={() => setView("build")}
            >
              Wizard
            </button>
            {onOpenBuilder && (
              <button className="launcher-rail-btn" onClick={onOpenBuilder}>
                Session Builder
              </button>
            )}
          </nav>

          {view === "launch" && (
            <section className="launcher-content" aria-label="Launch a session">
              <p className="launcher-hint">
                Sessions in your current catalog scope, deployable as authored. Building blocks
                for your own sessions live in the Build tab.
              </p>
              <div className="launcher-sessions">
                {sessions.map((s) => (
                  <div key={s.source_id.session_ref} className="launcher-row-shell">
                    <button
                      className="launcher-row"
                      onClick={() => {
                        if (s.active || deploying || !s.deploy_allowed) return;
                        onLaunchSession(s);
                        onDeployStarted();
                      }}
                      disabled={s.active || deploying || !s.deploy_allowed}
                      title={
                        s.active
                          ? "Already running"
                          : s.deploy_allowed
                            ? `Deploy ${s.name}`
                            : s.blockers?.[0]?.message ?? "This session cannot be deployed"
                      }
                    >
                      <span className="launcher-row-name">{s.name}</span>
                      <span className="launcher-row-desc">{s.constellation}</span>
                      <span className="launcher-row-meta">
                        {s.routing_stack && <Badge>{s.routing_stack}</Badge>}
                        {s.active && <Badge tone="ok">running</Badge>}
                        {!s.deploy_allowed && <Badge>unavailable</Badge>}
                      </span>
                    </button>
                    <IconButton
                      icon="download"
                      label={`Download ${s.name} YAML`}
                      disabled={downloadingFile !== null}
                      onClick={() => void downloadSessionYaml(s.source_id, s.name)}
                    />
                  </div>
                ))}
                {downloadError && <div className="wizard-error">{downloadError}</div>}
                {sessions.length === 0 && (
                  <div className="launcher-empty">No catalog sessions found.</div>
                )}
              </div>
              <div className="launcher-upload">
                <Button icon="download" onClick={() => fileInputRef.current?.click()} disabled={deploying}>
                  Import and deploy session YAML…
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".yaml,.yml"
                  onChange={handleUpload}
                  className="launcher-upload-input"
                  aria-label="Session YAML file"
                />
                <span className="launcher-hint">
                  Referenced components must already exist in your catalog. Import a complete user:
                  closure in Session Builder.
                </span>
              </div>
              {uploadError && <div className="wizard-error">{uploadError}</div>}
              {view === "launch" && wizard.error && <div className="wizard-error">{wizard.error}</div>}
            </section>
          )}

          {view === "build" && (
            <section className="launcher-content" aria-label="Build a session">
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

              {wizard.error && <div className="wizard-error">{wizard.error}</div>}

              {isGroupA && !wizard.coveragePreview && (
                <SelectionCards
                  presets={wizard.presets}
                  customConstellationCapability={wizard.customConstellationCapability}
                  customConstellationSeed={wizard.customConstellationSeed}
                  customConstellationDefaultNode={wizard.customConstellationDefaultNode}
                  customConstellationPatterns={wizard.customConstellationPatterns}
                  orbitModels={wizard.orbitModels}
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
                  onDeriveConstellationLayout={wizard.deriveConstellationLayout}
                  onPreview={wizard.previewCoverage}
                  onContinueWithoutPreview={wizard.continueToProtocol}
                  canPreview={allGroupASelected}
                  previewing={wizard.previewing}
                />
              )}

              {isGroupA && wizard.coveragePreview && (
                <CoveragePreview
                  result={wizard.coveragePreview}
                  onContinue={wizard.continueToProtocol}
                  onBack={wizard.clearPreview}
                />
              )}

              {wizard.state.step === "protocol" && (
                <div className="wizard-panel">
                  <h2 className="wizard-panel-title">Select Routing Protocol</h2>
                  <ProtocolSelection
                    selected={wizard.state.protocol}
                    rules={wizard.rules}
                    onSelect={wizard.selectProtocol}
                  />
                  <div className="wizard-nav">
                    <button className="wizard-nav-btn" onClick={() => wizard.goToStep("selections" as WizardStep)}>
                      Back to Configuration
                    </button>
                  </div>
                </div>
              )}

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

              {wizard.state.step === "review" && (
                <ReviewPanel
                  state={wizard.state}
                  orbitModels={wizard.orbitModels}
                  generatedYaml={wizard.generatedYaml}
                  generating={wizard.generating}
                  deploying={wizard.deploying || deploying}
                  exporting={wizard.exporting}
                  onBack={wizard.goBack}
                  onDeploy={handleDeploy}
                  onDownload={handleDownload}
                  onReset={wizard.reset}
                />
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
