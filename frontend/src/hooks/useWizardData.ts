// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Wizard data fetching — loads presets, satellite primitives, GS sets,
 * stations, and extension rules.
 *
 * Extracted from useWizard.ts. Pure data loading, no state mutations beyond
 * storing the fetched data.
 */

import { useState, useEffect } from "react";
import {
  getWizardAvailableStations,
  getWizardConstellationPresets,
  getWizardExtensionRules,
  getWizardGroundStationSets,
  getWizardSatelliteTypes,
} from "../builder/builderApiClient";
import type {
  ConstellationPreset,
  ExtensionRules,
  SatelliteTypePreset,
  GroundStationSet,
  AvailableStation,
  WizardConstellationCapability,
  WizardConstellationGeometry,
  OrbitModel,
  WalkerPattern,
} from "../catalog/wizardTypes";
import { apiErrorFromException } from "../ui/apiError";

/** Whether the Wizard's authoring facts have loaded from VS-API. A failed load
 *  names every source that failed and the reason VS-API gave. */
export type WizardAuthoringStatus =
  | { readonly state: "loading" }
  | { readonly state: "ready" }
  | { readonly state: "failed"; readonly failures: readonly string[] };

export interface WizardData {
  authoring: WizardAuthoringStatus;
  presets: ConstellationPreset[];
  customConstellationCapability: WizardConstellationCapability | null;
  customConstellationSeed: WizardConstellationGeometry | null;
  customConstellationDefaultNode: string | null;
  customConstellationPatterns: WalkerPattern[];
  orbitModels: OrbitModel[];
  rules: ExtensionRules | null;
  satelliteTypes: SatelliteTypePreset[];
  groundStationSets: GroundStationSet[];
  availableStations: AvailableStation[];
}

export function useWizardData(): WizardData {
  const [presets, setPresets] = useState<ConstellationPreset[]>([]);
  const [customConstellationCapability, setCustomConstellationCapability] =
    useState<WizardConstellationCapability | null>(null);
  const [customConstellationSeed, setCustomConstellationSeed] =
    useState<WizardConstellationGeometry | null>(null);
  const [customConstellationDefaultNode, setCustomConstellationDefaultNode] =
    useState<string | null>(null);
  const [customConstellationPatterns, setCustomConstellationPatterns] =
    useState<WalkerPattern[]>([]);
  const [orbitModels, setOrbitModels] = useState<OrbitModel[]>([]);
  const [rules, setRules] = useState<ExtensionRules | null>(null);
  const [satelliteTypes, setSatelliteTypes] = useState<SatelliteTypePreset[]>([]);
  const [groundStationSets, setGroundStationSets] = useState<GroundStationSet[]>([]);
  const [availableStations, setAvailableStations] = useState<AvailableStation[]>([]);

  const [authoring, setAuthoring] = useState<WizardAuthoringStatus>({ state: "loading" });

  useEffect(() => {
    let current = true;
    const failures: string[] = [];
    // Each source either applies its facts or names why it failed, including a
    // response it cannot apply; a failed source is reported, never shown as an
    // empty list.
    const load = async <T,>(source: string, request: Promise<T>, apply: (value: T) => void) => {
      try {
        const value = await request;
        if (current) apply(value);
      } catch (err: unknown) {
        failures.push(`${source}: ${apiErrorFromException(err)}`);
      }
    };

    void Promise.all([
      load("constellation presets", getWizardConstellationPresets(), (data) => {
        setPresets(data.presets.map((preset) => ({ ...preset, custom_geometry: null })));
        setCustomConstellationCapability(data.custom_geometry);
        setCustomConstellationSeed(data.custom_geometry_seed);
        setCustomConstellationDefaultNode(data.custom_geometry_default_node);
        setCustomConstellationPatterns([...data.custom_geometry_patterns]);
        setOrbitModels([...data.orbit_models]);
      }),
      load("routing choices", getWizardExtensionRules(), setRules),
      load("satellite node models", getWizardSatelliteTypes(), (data) =>
        setSatelliteTypes([...data.presets]),
      ),
      load("ground station sets", getWizardGroundStationSets(), (data) =>
        setGroundStationSets(data.presets.map((preset) => ({ ...preset }))),
      ),
      load("ground stations", getWizardAvailableStations(), (data) =>
        setAvailableStations([...data.stations]),
      ),
    ]).then(() => {
      if (!current) return;
      setAuthoring(failures.length > 0 ? { state: "failed", failures } : { state: "ready" });
    });
    return () => {
      current = false;
    };
  }, []);

  return {
    authoring,
    presets,
    customConstellationCapability,
    customConstellationSeed,
    customConstellationDefaultNode,
    customConstellationPatterns,
    orbitModels,
    rules,
    satelliteTypes,
    groundStationSets,
    availableStations,
  };
}
