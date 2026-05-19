// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Types for the M8 session wizard.
 *
 * Step model: Group A (selections — any order, preview gates on all three)
 * followed by Group B (protocol, extensions) after preview.
 */

// --- Library presets (fetched from VS-API) ---

export interface ConstellationPreset {
  name: string;
  description: string;
  satellite_count: number;
  constellation: string; // file path — used by generate endpoint
  ground_stations: string; // preset default GS — NOT used for wizard selection
  mode?: ConstellationMode | string | null;
}

export interface SatelliteTypePreset {
  name: string;
  description: string | null;
  isl_terminals: IslTerminalDef[];
  ground_terminals: GroundTerminalDef[];
}

export interface IslTerminalDef {
  type: string;
  band?: string;
  count: number;
  role?: string;
  max_range_km: number;
  bandwidth_mbps: number;
  max_tracking_rate_deg_s: number;
  field_of_regard_deg?: number;
}

export interface GroundTerminalDef {
  type: string;
  band?: string;
  count: number;
  bandwidth_mbps: number;
}

export interface GroundStationSet {
  name: string;
  description: string;
  stations: string[];
  file: string | null; // set file path, or null for custom station picks
}

export interface AvailableStation {
  name: string;
  lat_deg: number;
  lon_deg: number;
}

export interface ExtensionRules {
  protocols: Record<
    string,
    {
      extensions: string[];
      constraints: Record<string, string[]>;
    }
  >;
  area_strategies: string[];
}

export type Protocol = "ospf" | "isis" | "nodalpath";
export type ConstellationMode = "parametric" | "explicit" | "tle";
export type OrbitPropagator = "keplerian-circular" | "j2-mean-elements" | "sgp4-tle";

// --- Coverage preview (returned by POST /api/v1/session/preview-coverage) ---

export interface CoveragePreviewResult {
  orbital_period_s: number;
  preview_step_s: number;
  isl: {
    total_possible: number;
    formed_at_least_once: number;
    never_formed: number;
    feasibility_pct: number;
    min_active: number;
    max_active: number;
    failure_reasons?: {
      range_exceeded: number;
      tracking_exceeded: number;
      field_of_regard: number;
      los_blocked: number;
      polar_seam: number;
      terminal_exhausted: number;
    };
  };
  ground_stations: {
    per_station: Record<string, { coverage_pct: number; longest_gap_s: number; reason?: string | null }>;
    simultaneous_min: number;
    simultaneous_max: number;
    simultaneous_mean: number;
    max_gap_s: number;
  };
  warnings: Array<{ severity: string; message: string } | string>;
}

// --- Step model ---

/** Group A: independent selections (any order). Preview gates on all three.
 *  Group B: protocol + extensions, after preview. */
export type WizardPhase = "selections" | "preview" | "protocol" | "extensions" | "review";

/** Which selection card is currently expanded in group A. */
export type ActiveCard = "constellation" | "satellite-type" | "ground-stations" | "orbit-model" | null;

export interface RoutingTimers {
  bfd: boolean;
  bfd_detect_multiplier: number;
  bfd_rx_interval: number;
  bfd_tx_interval: number;
  isis_hello_interval: number;
  isis_hello_multiplier: number;
  spf_init_delay: number;
  spf_short_delay: number;
  spf_long_delay: number;
  spf_holddown: number;
  spf_time_to_learn: number;
  ospf_hello_interval: number;
  ospf_dead_interval: number;
  ospf_spf_delay: number;
  ospf_spf_initial_hold: number;
  ospf_spf_max_hold: number;
}

export const DEFAULT_ROUTING_TIMERS: RoutingTimers = {
  bfd: false,
  bfd_detect_multiplier: 3,
  bfd_rx_interval: 300,
  bfd_tx_interval: 300,
  isis_hello_interval: 1,
  isis_hello_multiplier: 3,
  spf_init_delay: 50,
  spf_short_delay: 200,
  spf_long_delay: 1000,
  spf_holddown: 2000,
  spf_time_to_learn: 500,
  ospf_hello_interval: 1,
  ospf_dead_interval: 3,
  ospf_spf_delay: 50,
  ospf_spf_initial_hold: 200,
  ospf_spf_max_hold: 1000,
};

export interface WizardState {
  phase: WizardPhase;
  activeCard: ActiveCard;

  // Group A — independent, any order
  constellation: ConstellationPreset | null;
  satelliteType: SatelliteTypePreset | null;
  groundStationSet: GroundStationSet | null;

  // Coverage preview result (null = not yet run)
  coveragePreview: CoveragePreviewResult | null;

  // Orbit propagation model
  orbitPropagator: OrbitPropagator;

  // Group B — after preview
  protocol: Protocol | null;
  extensions: string[];
  areaStrategy: string;
  routingTimers: RoutingTimers;
}

export type WizardStep =
  | "selections"
  | "satellite-type"
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
  orbitPropagator: OrbitPropagator;
  protocol: Protocol | null;
  extensions: string[];
  areaStrategy: string;
  routingTimers: RoutingTimers;
}
