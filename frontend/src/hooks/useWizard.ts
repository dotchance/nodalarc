// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Wizard orchestrator — composes data, navigation, and API hooks.
 *
 * This is a thin composition layer. Each concern lives in its own hook:
 * - useWizardData: fetches presets, satellite types, GS sets, stations
 * - useWizardNav: step navigation (goToStep, goBack, goToReview)
 * - useWizardApi: generate, deploy, preview-coverage API calls
 */

import { useState, useCallback } from "react";
import type {
  ConstellationPreset,
  Protocol,
  OrbitPropagator,
  RoutingTimers,
  SatelliteTypePreset,
  GroundStationSet,
  WizardRuntimeState,
  WizardStep,
} from "../catalog/wizardTypes";
import { DEFAULT_ROUTING_TIMERS } from "../catalog/wizardTypes";
import {
  DEFAULT_ORBIT_PROPAGATOR,
  constellationUnsupportedReason,
  defaultOrbitPropagatorForConstellation,
  supportedOrbitModelsForConstellation,
} from "../catalog/orbitModels";
import { useWizardData } from "./useWizardData";
import { useWizardNav } from "./useWizardNav";
import { useWizardApi } from "./useWizardApi";

export function useWizard() {
  const data = useWizardData();
  const api = useWizardApi();

  const [state, setState] = useState<WizardRuntimeState>({
    step: "selections" as WizardStep,
    satelliteType: null,
    groundStationSet: null,
    constellation: null,
    orbitPropagator: DEFAULT_ORBIT_PROPAGATOR,
    protocol: null,
    extensions: [],
    areaStrategy: "flat",
    routingTimers: { ...DEFAULT_ROUTING_TIMERS },
  });

  const nav = useWizardNav(setState);

  // --- Selection callbacks (update state + advance step) ---

  const selectSatelliteType = useCallback((preset: SatelliteTypePreset) => {
    setState((s) => ({ ...s, satelliteType: preset }));
    api.clearYaml();
    api.clearError();
  }, [api]);

  const selectGroundStationSet = useCallback((set: GroundStationSet) => {
    setState((s) => ({ ...s, groundStationSet: set }));
    api.clearYaml();
    api.clearError();
  }, [api]);

  const selectCustomGroundStations = useCallback((stationNames: string[]) => {
    const customSet: GroundStationSet = {
      name: "custom",
      description: `Custom selection: ${stationNames.length} stations`,
      stations: stationNames,
      file: null,
    };
    setState((s) => ({ ...s, groundStationSet: customSet }));
    api.clearYaml();
    api.clearError();
  }, [api]);

  const selectConstellation = useCallback((preset: ConstellationPreset) => {
    if (constellationUnsupportedReason(preset)) {
      return;
    }
    setState((s) => {
      const supported = supportedOrbitModelsForConstellation(preset).map((option) => option.id);
      const orbitPropagator = supported.includes(s.orbitPropagator)
        ? s.orbitPropagator
        : defaultOrbitPropagatorForConstellation(preset);
      return { ...s, constellation: preset, orbitPropagator };
    });
    api.clearYaml();
    api.clearError();
  }, [api]);

  const selectOrbitPropagator = useCallback((orbitPropagator: OrbitPropagator) => {
    setState((s) => {
      const supported = supportedOrbitModelsForConstellation(s.constellation).map(
        (option) => option.id,
      );
      if (!supported.includes(orbitPropagator)) {
        return s;
      }
      return { ...s, orbitPropagator };
    });
    api.clearYaml();
    api.clearError();
  }, [api]);

  /** Advance from selections to protocol step (after preview or skip). */
  const continueToProtocol = useCallback(() => {
    setState((s) => ({ ...s, step: "protocol" as WizardStep }));
  }, []);

  const selectProtocol = useCallback((protocol: Protocol) => {
    setState((s) => {
      const nextStep: WizardStep = protocol === "nodalpath" ? "review" : "extensions";
      return { ...s, protocol, extensions: [], step: nextStep };
    });
    api.clearYaml();
    api.clearError();
  }, [api]);

  const toggleExtension = useCallback((ext: string) => {
    setState((s) => {
      const has = s.extensions.includes(ext);
      let next = has ? s.extensions.filter((e) => e !== ext) : [...s.extensions, ext];
      if (has && ext === "te") {
        next = next.filter((e) => e !== "mpls");
      }
      if (!has && ext === "mpls" && !next.includes("te")) {
        next.push("te");
      }
      return { ...s, extensions: next };
    });
    api.clearYaml();
  }, [api]);

  const setAreaStrategy = useCallback((strategy: string) => {
    setState((s) => ({ ...s, areaStrategy: strategy }));
    api.clearYaml();
  }, [api]);

  const updateTimers = useCallback((patch: Partial<RoutingTimers>) => {
    setState((s) => ({
      ...s,
      routingTimers: { ...s.routingTimers, ...patch },
    }));
    api.clearYaml();
  }, [api]);

  // --- Extension constraint checks ---

  const isExtensionAllowed = useCallback(
    (ext: string): boolean => {
      if (!data.rules || !state.protocol) return false;
      const protoRules = data.rules.protocols[state.protocol];
      if (!protoRules) return false;
      return protoRules.extensions.includes(ext);
    },
    [data.rules, state.protocol],
  );

  const isExtensionEnabled = useCallback(
    (ext: string): boolean => {
      if (!isExtensionAllowed(ext)) return false;
      if (!data.rules || !state.protocol) return false;
      const protoRules = data.rules.protocols[state.protocol];
      const deps = protoRules?.constraints[ext];
      if (!deps) return true;
      return deps.every((d) => state.extensions.includes(d));
    },
    [data.rules, state.protocol, state.extensions, isExtensionAllowed],
  );

  // --- API wrappers that pass current state ---

  const generate = useCallback(() => api.generate(state), [api, state]);

  const previewCoverage = useCallback(() => api.previewCoverage(state), [api, state]);

  const reset = useCallback(() => {
    setState({
      step: "selections" as WizardStep,
      satelliteType: null,
      groundStationSet: null,
      constellation: null,
      orbitPropagator: DEFAULT_ORBIT_PROPAGATOR,
      protocol: null,
      extensions: [],
      areaStrategy: "flat",
      routingTimers: { ...DEFAULT_ROUTING_TIMERS },
    });
    api.clearYaml();
    api.clearError();
  }, [api]);

  return {
    // Data
    presets: data.presets,
    rules: data.rules,
    satelliteTypes: data.satelliteTypes,
    groundStationSets: data.groundStationSets,
    availableStations: data.availableStations,
    // State
    state,
    generating: api.generating,
    deploying: api.deploying,
    generatedYaml: api.generatedYaml,
    error: api.error,
    // Selection
    selectSatelliteType,
    selectGroundStationSet,
    selectCustomGroundStations,
    selectConstellation,
    selectOrbitPropagator,
    selectProtocol,
    toggleExtension,
    setAreaStrategy,
    updateTimers,
    // Navigation
    goToStep: nav.goToStep,
    goBack: nav.goBack,
    goToReview: nav.goToReview,
    // Extension checks
    isExtensionAllowed,
    isExtensionEnabled,
    // API
    generate,
    deploy: api.deploy,
    previewCoverage,
    previewing: api.previewing,
    coveragePreview: api.coveragePreview,
    clearPreview: api.clearPreview,
    continueToProtocol,
    reset,
  };
}
