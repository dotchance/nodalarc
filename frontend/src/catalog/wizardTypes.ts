// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Types for the session wizard.
 *
 * Step model: Group A (selections — any order, preview gates on all three)
 * followed by Group B (protocol, extensions) after preview.
 */

import type {
  CoveragePreviewResult,
  WizardConstellationGeometry,
  WizardConstellationPreset as BackendConstellationPreset,
  WizardGroundStationSetPreset as BackendGroundStationSetPreset,
  WizardAreaStrategy,
  WizardOrbitPropagator,
  WizardOrbitModelMetadata,
  WizardRoutingTimerIntent,
  WizardSessionIntent,
  WizardSatelliteTypePreset,
  WizardAvailableStation,
  WizardExtensionRulesResponse,
  WizardExtension,
  WizardWalkerPatternMetadata,
} from "../builder/generated/builderApi";

export type {
  CoveragePreviewResult,
  WizardConstellationCapability,
  WizardConstellationGeometry,
  WizardConstellationPresetResponse,
  WizardGroundStationSetPreset,
  WizardOrbitModelMetadata,
  WizardSatelliteTypePreset,
  WizardAvailableStation,
  WizardExtensionRulesResponse,
  WizardExtension,
  WizardExtensionMetadata,
  WizardProtocolMetadata,
  WizardRoutingBooleanField,
  WizardRoutingTimerField,
  WizardRoutingTimerFieldMetadata,
  WizardWalkerPattern,
  WizardWalkerPatternMetadata,
} from "../builder/generated/builderApi";

// --- Library presets (fetched from VS-API) ---

export type ConstellationPreset =
  | (BackendConstellationPreset & { custom_geometry?: null })
  | {
      name: string;
      description: string;
      satellite_count: number;
      constellation: null;
      default_node: string | null;
      capability: BackendConstellationPreset["capability"];
      custom_geometry: WizardConstellationGeometry;
    };

/** A space node primitive — the satellite that flies a constellation's
 *  geometry. Sessions assemble from primitives: choosing a constellation
 *  picks geometry plus a default node; this overrides the node. */
export type SatelliteTypePreset = WizardSatelliteTypePreset;

export interface GroundStationSet extends Omit<BackendGroundStationSetPreset, "file"> {
  file: BackendGroundStationSetPreset["file"] | null;
  custom_site_refs?: string[];
}

export type AvailableStation = WizardAvailableStation;
export type ExtensionRules = WizardExtensionRulesResponse;

export type Protocol = WizardSessionIntent["protocol"];
export type OrbitPropagator = WizardOrbitPropagator;
export type OrbitModel = WizardOrbitModelMetadata;
export type WalkerPattern = WizardWalkerPatternMetadata;
export type AreaStrategy = WizardAreaStrategy;

// --- Step model ---

/** Group A: independent selections (any order). Preview gates on all three.
 *  Group B: protocol + extensions, after preview. */
export type WizardPhase = "selections" | "preview" | "protocol" | "extensions" | "review";

/** Which selection card is currently expanded in group A. */
export type ActiveCard = "constellation" | "satellite" | "ground-stations" | "orbit-model" | null;

export type RoutingTimers = WizardRoutingTimerIntent;

export interface WizardState {
  phase: WizardPhase;
  activeCard: ActiveCard;

  // Group A — independent, any order. The satellite is optional: null
  // means the constellation's own default node flies.
  constellation: ConstellationPreset | null;
  satelliteType: SatelliteTypePreset | null;
  groundStationSet: GroundStationSet | null;

  // Coverage preview result (null = not yet run)
  coveragePreview: CoveragePreviewResult | null;

  // Orbit propagation model
  orbitPropagator: OrbitPropagator | null;

  // Group B — after preview
  protocol: Protocol | null;
  extensions: WizardExtension[];
  areaStrategy: AreaStrategy | null;
  routingTimers: RoutingTimers | null;
}

export type WizardStep =
  | "selections"
  | "ground-stations"
  | "constellation"
  | "protocol"
  | "extensions"
  | "review";

export interface WizardRuntimeState {
  step: WizardStep;
  satelliteType: SatelliteTypePreset | null;
  groundStationSet: GroundStationSet | null;
  constellation: ConstellationPreset | null;
  orbitPropagator: OrbitPropagator | null;
  protocol: Protocol | null;
  extensions: WizardExtension[];
  areaStrategy: AreaStrategy | null;
  routingTimers: RoutingTimers | null;
}
