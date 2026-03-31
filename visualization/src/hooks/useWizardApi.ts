/** Wizard API calls — generate, deploy, preview-coverage.
 *
 * Extracted from useWizard.ts. Each function manages its own loading/error
 * state and calls the corresponding VS-API endpoint.
 */

import { useState, useCallback } from "react";
import { REST_URL, authHeaders } from "../config";
import type { LegacyWizardState, CoveragePreviewResult } from "../catalog/wizardTypes";

/** If the constellation field is a JSON string (custom), parse it to a dict.
 *  Otherwise return the file path string as-is. */
function resolveConstellation(raw: string): string | Record<string, unknown> {
  if (raw.startsWith("{")) {
    try { return JSON.parse(raw); } catch { /* fall through */ }
  }
  return raw;
}

export interface WizardApiState {
  generating: boolean;
  deploying: boolean;
  previewing: boolean;
  generatedYaml: string | null;
  coveragePreview: CoveragePreviewResult | null;
  error: string | null;
}

export function useWizardApi() {
  const [generating, setGenerating] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [generatedYaml, setGeneratedYaml] = useState<string | null>(null);
  const [coveragePreview, setCoveragePreview] = useState<CoveragePreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const clearError = useCallback(() => setError(null), []);
  const clearYaml = useCallback(() => setGeneratedYaml(null), []);
  const clearPreview = useCallback(() => setCoveragePreview(null), []);

  const generate = useCallback(
    async (state: LegacyWizardState) => {
      if (!state.constellation || !state.protocol) return;
      setGenerating(true);
      setError(null);
      try {
        const constellationValue = resolveConstellation(state.constellation.constellation);
        const isCustomConstellation = typeof constellationValue !== "string";
        const resp = await fetch(`${REST_URL}/api/v1/session/generate`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            constellation: state.constellation.name,
            protocol: state.protocol,
            extensions: state.extensions,
            area_strategy: state.areaStrategy,
            ground_stations: state.groundStationSet?.file
              ? state.groundStationSet.file
              : state.groundStationSet?.stations ?? undefined,
            satellite_type: state.satelliteType?.name ?? undefined,
            custom_constellation: isCustomConstellation ? constellationValue : undefined,
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          setError(data.error || "Generation failed");
        } else {
          setGeneratedYaml(data.yaml);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Generation failed");
      } finally {
        setGenerating(false);
      }
    },
    [],
  );

  const deploy = useCallback(
    async (yaml: string): Promise<boolean> => {
      setDeploying(true);
      setError(null);
      try {
        const resp = await fetch(`${REST_URL}/api/v1/session/deploy`, {
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
    },
    [],
  );

  const previewCoverage = useCallback(
    async (state: LegacyWizardState) => {
      if (!state.constellation || !state.satelliteType || !state.groundStationSet) return;
      setPreviewing(true);
      setError(null);
      try {
        const constellationValue = resolveConstellation(state.constellation.constellation);
        const resp = await fetch(`${REST_URL}/api/v1/session/preview-coverage`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            constellation: constellationValue,
            satellite_type: state.satelliteType.name,
            ground_stations: state.groundStationSet.file
              ? state.groundStationSet.file
              : state.groundStationSet.stations,
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          setError(data.error || "Preview failed");
        } else {
          setCoveragePreview(data as CoveragePreviewResult);
        }
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
    generatedYaml,
    coveragePreview,
    error,
    clearError,
    clearYaml,
    clearPreview,
    generate,
    deploy,
    previewCoverage,
  };
}
