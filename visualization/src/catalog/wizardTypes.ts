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
  };
  ground_stations: {
    per_station: Record<string, { coverage_pct: number; longest_gap_s: number }>;
    simultaneous_min: number;
    simultaneous_max: number;
    simultaneous_mean: number;
    max_gap_s: number;
  };
  warnings: string[];
}

// --- Step model ---

/** Group A: independent selections (any order). Preview gates on all three.
 *  Group B: protocol + extensions, after preview. */
export type WizardPhase = "selections" | "preview" | "protocol" | "extensions" | "review";

/** Which selection card is currently expanded in group A. */
export type ActiveCard = "constellation" | "satellite-type" | "ground-stations" | null;

export interface WizardState {
  phase: WizardPhase;
  activeCard: ActiveCard;

  // Group A — independent, any order
  constellation: ConstellationPreset | null;
  satelliteType: SatelliteTypePreset | null;
  groundStationSet: GroundStationSet | null;

  // Coverage preview result (null = not yet run)
  coveragePreview: CoveragePreviewResult | null;

  // Group B — after preview
  protocol: Protocol | null;
  extensions: string[];
  areaStrategy: string;
}

// --- Backward compatibility during refactor ---
// The old linear wizard used WizardStep and a different WizardState shape.
// These aliases keep existing code compiling during the panel extraction.
// They will be removed when the refactor is complete.

/** @deprecated Use WizardPhase instead. */
export type WizardStep =
  | "satellite-type"
  | "ground-stations"
  | "constellation"
  | "protocol"
  | "extensions"
  | "review";

/** @deprecated Use WizardState instead. */
export interface LegacyWizardState {
  step: WizardStep;
  satelliteType: SatelliteTypePreset | null;
  groundStationSet: GroundStationSet | null;
  constellation: ConstellationPreset | null;
  protocol: Protocol | null;
  extensions: string[];
  areaStrategy: string;
}
