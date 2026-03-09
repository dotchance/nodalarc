/** Types for the session/scenario manifest (catalog UI). */

export interface ManifestSession {
  id: string;
  name: string;
  description: string;
  constellation: string;
  satellite_count: number;
  routing_stack: string;
  ground_station_set: string;
  tags: string[];
}

export interface ManifestScenario {
  id: string;
  name: string;
  description: string;
  compatible_sessions: string[];
  duration_minutes: number;
  tags: string[];
}

export interface Manifest {
  sessions: ManifestSession[];
  scenarios: ManifestScenario[];
}
