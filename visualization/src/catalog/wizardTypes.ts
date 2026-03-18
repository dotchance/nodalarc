/** Types for the session wizard. */

export interface ConstellationPreset {
  name: string;
  description: string;
  satellite_count: number;
  constellation: string;
  ground_stations: string;
}

export type Protocol = "ospf" | "isis" | "nodalpath";

export interface ExtensionRules {
  protocols: Record<string, {
    extensions: string[];
    constraints: Record<string, string[]>;
  }>;
  area_strategies: string[];
}

export interface SatelliteTypePreset {
  name: string;
  description: string | null;
  isl_terminals: Array<{
    type: string;
    band?: string;
    count: number;
    role?: string;
    max_range_km: number;
    bandwidth_mbps: number;
    max_tracking_rate_deg_s: number;
    field_of_regard_deg?: number;
  }>;
  ground_terminals: Array<{
    type: string;
    band?: string;
    count: number;
    bandwidth_mbps: number;
  }>;
}

export interface GroundStationSet {
  name: string;
  description: string;
  stations: string[];
  file: string;
}

export interface AvailableStation {
  name: string;
  lat_deg: number;
  lon_deg: number;
}

export type WizardStep = "satellite-type" | "ground-stations" | "constellation" | "protocol" | "extensions" | "review";

export interface WizardState {
  step: WizardStep;
  satelliteType: SatelliteTypePreset | null;
  groundStationSet: GroundStationSet | null;
  constellation: ConstellationPreset | null;
  protocol: Protocol | null;
  extensions: string[];
  areaStrategy: string;
}
