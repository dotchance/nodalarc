// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Wizard API calls — generate, deploy, preview-coverage.
 *
 * Extracted from useWizard.ts. Each function manages its own loading/error
 * state and calls the corresponding VS-API endpoint.
 */

import { useState, useCallback, useRef } from "react";
import { REST_URL, authHeaders } from "../config";
import type { WizardRuntimeState, CoveragePreviewResult } from "../catalog/wizardTypes";
import {
  BuilderApiError,
  compileWizardSession,
  deployBuilderSession,
  deriveVisualWalkerLayout,
  exportCatalogSession,
  previewWizardCoverage,
  saveBuilderSession,
} from "../builder/builderApiClient";
import type {
  BuilderCompileResult,
  BuilderSessionSaveResult,
  CatalogSessionExport,
  BuilderVisualWalkerLayoutRequest,
  BuilderVisualWalkerLayoutResult,
} from "../builder/generated/builderApi";

export interface WizardApiState {
  generating: boolean;
  deploying: boolean;
  previewing: boolean;
  exporting: boolean;
  generatedYaml: string | null;
  coveragePreview: CoveragePreviewResult | null;
  error: string | null;
}

export function useWizardApi() {
  const [generating, setGenerating] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [generatedYaml, setGeneratedYaml] = useState<string | null>(null);
  const [coveragePreview, setCoveragePreview] = useState<CoveragePreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<BuilderCompileResult | null>(null);
  const [saved, setSaved] = useState<BuilderSessionSaveResult | null>(null);
  const draftRevision = useRef(0);

  const clearError = useCallback(() => setError(null), []);
  const clearYaml = useCallback(() => {
    setGeneratedYaml(null);
    setCompiled(null);
    setSaved(null);
  }, []);
  const clearPreview = useCallback(() => setCoveragePreview(null), []);
  const deriveConstellationLayout = useCallback(
    (
      intent: BuilderVisualWalkerLayoutRequest,
    ): Promise<BuilderVisualWalkerLayoutResult> => deriveVisualWalkerLayout(intent),
    [],
  );

  const generate = useCallback(
    async (state: WizardRuntimeState) => {
      if (
        !state.constellation ||
        !state.protocol ||
        !state.orbitPropagator ||
        !state.areaStrategy ||
        !state.routingTimers
      ) {
        setError("Wizard authoring defaults are unavailable");
        return;
      }
      setGenerating(true);
      setError(null);
      try {
        draftRevision.current += 1;
        const result = await compileWizardSession({
          draft_revision: draftRevision.current,
          intent: {
            constellation_ref: state.constellation.constellation,
            custom_constellation: state.constellation.custom_geometry ?? null,
            satellite_node_ref: state.satelliteType?.file ?? null,
            ground_site_set_ref: state.groundStationSet?.file ?? null,
            custom_site_refs: state.groundStationSet?.custom_site_refs ?? [],
            protocol: state.protocol,
            extensions: state.extensions,
            orbit_propagator: state.orbitPropagator,
            area_strategy: state.areaStrategy,
            routing_timers: state.routingTimers,
          },
        });
        setCompiled(result);
        setSaved(null);
        setGeneratedYaml(result.canonical_session_yaml ?? null);
        if (!result.save_verdict.allowed) {
          setError(result.save_verdict.blockers?.[0]?.message ?? "Wizard draft cannot be saved");
        }
      } catch (e) {
        setError(e instanceof BuilderApiError || e instanceof Error ? e.message : "Generation failed");
      } finally {
        setGenerating(false);
      }
    },
    [],
  );

  const deploy = useCallback(
    async (): Promise<boolean> => {
      if (!compiled) {
        setError("Generate and review the Wizard session before deployment");
        return false;
      }
      setDeploying(true);
      setError(null);
      try {
        const exactSaved = saved ?? await saveBuilderSession({
          draft: compiled.draft,
          target_ref: compiled.target_ref,
        });
        setSaved(exactSaved);
        if (!exactSaved.deploy_verdict.allowed) {
          setError(
            exactSaved.deploy_verdict.blockers?.[0]?.message ?? "Saved session cannot deploy",
          );
          return false;
        }
        await deployBuilderSession({
          session_ref: exactSaved.session.ref,
          expected_session_revision: exactSaved.session.revision,
          expected_document_digest: exactSaved.digests.document,
          expected_dependency_digest: exactSaved.digests.dependency,
        });
        return true;
      } catch (e) {
        setError(e instanceof BuilderApiError || e instanceof Error ? e.message : "Deploy failed");
        return false;
      } finally {
        setDeploying(false);
      }
    },
    [compiled, saved],
  );

  const exportClosure = useCallback(async (): Promise<CatalogSessionExport | null> => {
    if (!compiled) {
      setError("Generate and review the Wizard session before export");
      return null;
    }
    setExporting(true);
    setError(null);
    try {
      const exactSaved = saved ?? await saveBuilderSession({
        draft: compiled.draft,
        target_ref: compiled.target_ref,
      });
      setSaved(exactSaved);
      return await exportCatalogSession({
        session_ref: exactSaved.session.ref,
        expected_session_revision: exactSaved.session.revision,
      });
    } catch (e) {
      setError(e instanceof BuilderApiError || e instanceof Error ? e.message : "Export failed");
      return null;
    } finally {
      setExporting(false);
    }
  }, [compiled, saved]);

  const deployUploadedYaml = useCallback(async (yaml: string): Promise<boolean> => {
    setDeploying(true);
    setError(null);
    try {
      const resp = await fetch(`${REST_URL}/api/v1/session/deploy-from-yaml`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ yaml }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(data.error || "Deploy failed");
        return false;
      }
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Deploy failed");
      return false;
    } finally {
      setDeploying(false);
    }
  }, []);

  const previewCoverage = useCallback(
    async (state: WizardRuntimeState) => {
      if (!state.constellation || !state.groundStationSet || !state.orbitPropagator) {
        setError("Select a supported constellation orbit model before previewing");
        return;
      }
      setPreviewing(true);
      setError(null);
      try {
        const data = await previewWizardCoverage({
          intent: {
            constellation_ref: state.constellation.constellation,
            custom_constellation: state.constellation.custom_geometry ?? null,
            satellite_node_ref: state.satelliteType?.file ?? null,
            ground_site_set_ref: state.groundStationSet.file,
            custom_site_refs: state.groundStationSet.custom_site_refs ?? [],
            orbit_propagator: state.orbitPropagator,
          },
        });
        setCoveragePreview(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Preview failed");
      } finally {
        setPreviewing(false);
      }
    },
    [],
  );

  return {
    generating,
    deploying,
    previewing,
    exporting,
    generatedYaml,
    coveragePreview,
    error,
    clearError,
    clearYaml,
    clearPreview,
    deriveConstellationLayout,
    generate,
    deploy,
    exportClosure,
    deployUploadedYaml,
    previewCoverage,
  };
}
