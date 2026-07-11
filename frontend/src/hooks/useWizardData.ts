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

export interface WizardData {
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

  useEffect(() => {
    getWizardConstellationPresets()
      .then((data) => {
        setPresets(data.presets.map((preset) => ({ ...preset, custom_geometry: null })));
        setCustomConstellationCapability(data.custom_geometry);
        setCustomConstellationSeed(data.custom_geometry_seed);
        setCustomConstellationDefaultNode(data.custom_geometry_default_node);
        setCustomConstellationPatterns([...data.custom_geometry_patterns]);
        setOrbitModels([...data.orbit_models]);
      })
      .catch(() => {});

    getWizardExtensionRules()
      .then(setRules)
      .catch(() => {});

    getWizardSatelliteTypes()
      .then((data) => setSatelliteTypes([...data.presets]))
      .catch(() => {});

    getWizardGroundStationSets()
      .then((data) => setGroundStationSets(data.presets.map((preset) => ({ ...preset }))))
      .catch(() => {});

    getWizardAvailableStations()
      .then((data) => setAvailableStations([...data.stations]))
      .catch(() => {});
  }, []);

  return {
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
