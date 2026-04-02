/** Wizard data fetching — loads presets, satellite types, GS sets, stations, and extension rules.
 *
 * Extracted from useWizard.ts. Pure data loading, no state mutations beyond
 * storing the fetched data.
 */

import { useState, useEffect } from "react";
import { REST_URL, authHeaders } from "../config";
import type {
  ConstellationPreset,
  ExtensionRules,
  SatelliteTypePreset,
  GroundStationSet,
  AvailableStation,
} from "../catalog/wizardTypes";

export interface WizardData {
  presets: ConstellationPreset[];
  rules: ExtensionRules | null;
  satelliteTypes: SatelliteTypePreset[];
  groundStationSets: GroundStationSet[];
  availableStations: AvailableStation[];
}

export function useWizardData(): WizardData {
  const [presets, setPresets] = useState<ConstellationPreset[]>([]);
  const [rules, setRules] = useState<ExtensionRules | null>(null);
  const [satelliteTypes, setSatelliteTypes] = useState<SatelliteTypePreset[]>([]);
  const [groundStationSets, setGroundStationSets] = useState<GroundStationSet[]>([]);
  const [availableStations, setAvailableStations] = useState<AvailableStation[]>([]);

  useEffect(() => {
    fetch(`${REST_URL}/api/v1/presets/constellations`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: ConstellationPreset[]) => setPresets(data))
      .catch(() => {});

    fetch(`${REST_URL}/api/v1/wizard/extensions`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: ExtensionRules) => setRules(data))
      .catch(() => {});

    fetch(`${REST_URL}/api/v1/presets/satellite-types`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: SatelliteTypePreset[]) => setSatelliteTypes(data))
      .catch(() => {});

    fetch(`${REST_URL}/api/v1/presets/ground-stations`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: GroundStationSet[]) => setGroundStationSets(data))
      .catch(() => {});

    fetch(`${REST_URL}/api/v1/presets/ground-stations/stations`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((data: AvailableStation[]) => setAvailableStations(data))
      .catch(() => {});
  }, []);

  return { presets, rules, satelliteTypes, groundStationSets, availableStations };
}
