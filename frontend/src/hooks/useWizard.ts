// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Wizard orchestrator — composes data, navigation, and API hooks.
 *
 * This is a thin composition layer. Each concern lives in its own hook:
 * - useWizardData: fetches presets, GS sets, stations
 * - useWizardNav: step navigation (goToStep, goBack, goToReview)
 * - useWizardApi: generate, deploy, preview-coverage API calls
 */

import { useState, useCallback, useEffect } from "react";
import type {
  ConstellationPreset,
  Protocol,
  OrbitPropagator,
  RoutingTimers,
  SatelliteTypePreset,
  GroundStationSet,
  WizardRuntimeState,
  WizardStep,
  WizardExtension,
  AreaStrategy,
} from "../catalog/wizardTypes";
import {
  constellationUnsupportedReason,
  defaultOrbitPropagatorForConstellation,
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
    orbitPropagator: null,
    protocol: null,
    extensions: [],
    areaStrategy: null,
    routingTimers: null,
  });

  useEffect(() => {
    if (!data.rules) return;
    const defaults = data.rules;
    setState((current) => {
      if (current.areaStrategy !== null && current.routingTimers !== null) return current;
      return {
        ...current,
        areaStrategy: current.areaStrategy ?? defaults.default_area_strategy,
        routingTimers: current.routingTimers ?? { ...defaults.routing_timer_defaults },
      };
    });
  }, [data.rules]);

  const nav = useWizardNav(setState);

  // --- Selection callbacks (update state + advance step) ---

  const selectSatelliteType = useCallback((preset: SatelliteTypePreset | null) => {
    setState((s) => ({ ...s, satelliteType: preset }));
    api.clearYaml();
    api.clearError();
  }, [api]);

  const selectGroundStationSet = useCallback((set: GroundStationSet) => {
    setState((s) => ({ ...s, groundStationSet: set }));
    api.clearYaml();
    api.clearError();
  }, [api]);

  const selectCustomGroundStations = useCallback((siteRefs: string[]) => {
    const customSet: GroundStationSet = {
      name: "custom",
      description: `Custom selection: ${siteRefs.length} stations`,
      stations: siteRefs.map((ref) => {
        const parts = ref.split("/");
        return parts[parts.length - 1]?.replace(/\.ya?ml$/i, "") ?? ref;
      }),
      file: null,
      custom_site_refs: siteRefs,
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
      const supported = preset.capability.runtime_supported_propagators;
      const orbitPropagator = s.orbitPropagator && supported.includes(s.orbitPropagator)
        ? s.orbitPropagator
        : defaultOrbitPropagatorForConstellation(preset);
      return { ...s, constellation: preset, orbitPropagator };
    });
    api.clearYaml();
    api.clearError();
  }, [api]);

  const selectOrbitPropagator = useCallback((orbitPropagator: OrbitPropagator) => {
    setState((s) => {
      const supported = s.constellation?.capability.runtime_supported_propagators ?? [];
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
      return { ...s, protocol, extensions: [], step: "extensions" as WizardStep };
    });
    api.clearYaml();
    api.clearError();
  }, [api]);

  const toggleExtension = useCallback((ext: WizardExtension) => {
    setState((s) => {
      const has = s.extensions.includes(ext);
      const protoRules = s.protocol === "isis"
        ? data.rules?.protocols.isis
        : s.protocol === "ospf"
          ? data.rules?.protocols.ospf
          : null;
      let next = has ? s.extensions.filter((item) => item !== ext) : [...s.extensions, ext];
      if (protoRules) {
        let changed = true;
        while (changed) {
          const selected = new Set(next);
          const filtered = next.filter((item) =>
            (protoRules.constraints[item] ?? []).every((dependency) => selected.has(dependency))
          );
          changed = filtered.length !== next.length;
          next = filtered;
        }
      }
      return { ...s, extensions: next };
    });
    api.clearYaml();
  }, [api, data.rules]);

  const setAreaStrategy = useCallback((strategy: AreaStrategy) => {
    setState((s) => ({ ...s, areaStrategy: strategy }));
    api.clearYaml();
  }, [api]);

  const updateTimers = useCallback((patch: Partial<RoutingTimers>) => {
    setState((s) => ({
      ...s,
      routingTimers: s.routingTimers ? { ...s.routingTimers, ...patch } : s.routingTimers,
    }));
    api.clearYaml();
  }, [api]);

  // --- Extension constraint checks ---

  const isExtensionAllowed = useCallback(
    (ext: WizardExtension): boolean => {
      if (!data.rules || !state.protocol) return false;
      const protoRules = state.protocol === "isis"
        ? data.rules.protocols.isis
        : state.protocol === "ospf"
          ? data.rules.protocols.ospf
          : null;
      if (!protoRules) return false;
      return protoRules.extensions.includes(ext);
    },
    [data.rules, state.protocol],
  );

  const isExtensionEnabled = useCallback(
    (ext: WizardExtension): boolean => {
      if (!isExtensionAllowed(ext)) return false;
      if (!data.rules || !state.protocol) return false;
      const protoRules = state.protocol === "isis"
        ? data.rules.protocols.isis
        : state.protocol === "ospf"
          ? data.rules.protocols.ospf
          : null;
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
      orbitPropagator: null,
      protocol: null,
      extensions: [],
      areaStrategy: data.rules?.default_area_strategy ?? null,
      routingTimers: data.rules ? { ...data.rules.routing_timer_defaults } : null,
    });
    api.clearYaml();
    api.clearError();
  }, [api, data.rules]);

  return {
    // Data
    presets: data.presets,
    customConstellationCapability: data.customConstellationCapability,
    customConstellationSeed: data.customConstellationSeed,
    customConstellationDefaultNode: data.customConstellationDefaultNode,
    orbitModels: data.orbitModels,
    rules: data.rules,
    satelliteTypes: data.satelliteTypes,
    groundStationSets: data.groundStationSets,
    availableStations: data.availableStations,
    // State
    state,
    generating: api.generating,
    deploying: api.deploying,
    exporting: api.exporting,
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
    exportClosure: api.exportClosure,
    deployUploadedYaml: api.deployUploadedYaml,
    previewCoverage,
    previewing: api.previewing,
    coveragePreview: api.coveragePreview,
    clearPreview: api.clearPreview,
    continueToProtocol,
    reset,
  };
}
