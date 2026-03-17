/** Wizard state management hook. */

import { useState, useCallback, useEffect } from "react";
import { REST_URL, authHeaders } from "../config";
import type {
  ConstellationPreset,
  Protocol,
  ExtensionRules,
  SatelliteTypePreset,
  GroundStationSet,
  AvailableStation,
  WizardStep,
  WizardState,
} from "../catalog/wizardTypes";

export function useWizard() {
  const [presets, setPresets] = useState<ConstellationPreset[]>([]);
  const [rules, setRules] = useState<ExtensionRules | null>(null);
  const [satelliteTypes, setSatelliteTypes] = useState<SatelliteTypePreset[]>([]);
  const [groundStationSets, setGroundStationSets] = useState<GroundStationSet[]>([]);
  const [availableStations, setAvailableStations] = useState<AvailableStation[]>([]);
  const [state, setState] = useState<WizardState>({
    step: "satellite-type",
    satelliteType: null,
    groundStationSet: null,
    constellation: null,
    protocol: null,
    extensions: [],
    areaStrategy: "flat",
  });
  const [generating, setGenerating] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [generatedYaml, setGeneratedYaml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch presets, extension rules, satellite types, and ground station sets on mount
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

  const selectSatelliteType = useCallback((preset: SatelliteTypePreset) => {
    setState((s) => ({ ...s, satelliteType: preset, step: "ground-stations" }));
    setGeneratedYaml(null);
    setError(null);
  }, []);

  const selectGroundStationSet = useCallback((set: GroundStationSet) => {
    setState((s) => ({ ...s, groundStationSet: set, step: "constellation" }));
    setGeneratedYaml(null);
    setError(null);
  }, []);

  const selectCustomGroundStations = useCallback((stationNames: string[]) => {
    const customSet: GroundStationSet = {
      name: "custom",
      description: `Custom selection: ${stationNames.length} stations`,
      stations: stationNames,
      file: "",  // empty = pass as list[str] instead of file path
    };
    setState((s) => ({ ...s, groundStationSet: customSet, step: "constellation" }));
    setGeneratedYaml(null);
    setError(null);
  }, []);

  const selectConstellation = useCallback((preset: ConstellationPreset) => {
    setState((s) => ({ ...s, constellation: preset, step: "protocol" }));
    setGeneratedYaml(null);
    setError(null);
  }, []);

  const selectProtocol = useCallback((protocol: Protocol) => {
    setState((s) => {
      const nextStep: WizardStep = protocol === "nodalpath" ? "review" : "extensions";
      return { ...s, protocol, extensions: [], step: nextStep };
    });
    setGeneratedYaml(null);
    setError(null);
  }, []);

  const toggleExtension = useCallback((ext: string) => {
    setState((s) => {
      const has = s.extensions.includes(ext);
      let next = has ? s.extensions.filter((e) => e !== ext) : [...s.extensions, ext];
      // If removing TE, also remove MPLS (MPLS requires TE)
      if (has && ext === "te") {
        next = next.filter((e) => e !== "mpls");
      }
      // If adding MPLS, also add TE
      if (!has && ext === "mpls" && !next.includes("te")) {
        next.push("te");
      }
      return { ...s, extensions: next };
    });
    setGeneratedYaml(null);
  }, []);

  const setAreaStrategy = useCallback((strategy: string) => {
    setState((s) => ({ ...s, areaStrategy: strategy }));
    setGeneratedYaml(null);
  }, []);

  const goToStep = useCallback((step: WizardStep) => {
    setState((s) => ({ ...s, step }));
  }, []);

  const goBack = useCallback(() => {
    setState((s) => {
      if (s.step === "ground-stations") return { ...s, step: "satellite-type" };
      if (s.step === "constellation") return { ...s, step: "ground-stations" };
      if (s.step === "protocol") return { ...s, step: "constellation" };
      if (s.step === "extensions") return { ...s, step: "protocol" };
      if (s.step === "review") {
        return { ...s, step: s.protocol === "nodalpath" ? "protocol" : "extensions" };
      }
      return s;
    });
  }, []);

  const goToReview = useCallback(() => {
    setState((s) => ({ ...s, step: "review" }));
  }, []);

  /** Check if an extension is allowed for the current protocol. */
  const isExtensionAllowed = useCallback(
    (ext: string): boolean => {
      if (!rules || !state.protocol) return false;
      const protoRules = rules.protocols[state.protocol];
      if (!protoRules) return false;
      return protoRules.extensions.includes(ext);
    },
    [rules, state.protocol],
  );

  /** Check if an extension's dependencies are met. */
  const isExtensionEnabled = useCallback(
    (ext: string): boolean => {
      if (!isExtensionAllowed(ext)) return false;
      if (!rules || !state.protocol) return false;
      const protoRules = rules.protocols[state.protocol];
      const deps = protoRules?.constraints[ext];
      if (!deps) return true;
      return deps.every((d) => state.extensions.includes(d));
    },
    [rules, state.protocol, state.extensions, isExtensionAllowed],
  );

  const generate = useCallback(async () => {
    if (!state.constellation || !state.protocol) return;
    setGenerating(true);
    setError(null);
    try {
      const resp = await fetch(`${REST_URL}/api/v1/session/generate`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          constellation: state.constellation.name,
          protocol: state.protocol,
          extensions: state.extensions,
          area_strategy: state.areaStrategy,
          ground_stations: state.groundStationSet?.file
            ? state.groundStationSet.file
            : state.groundStationSet?.stations ?? undefined,
          satellite_type: state.satelliteType?.name ?? undefined,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setError(data.error || "Generation failed");
      } else {
        setGeneratedYaml(data.yaml);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  }, [state]);

  const deploy = useCallback(
    async (yaml: string): Promise<boolean> => {
      setDeploying(true);
      setError(null);
      try {
        const resp = await fetch(`${REST_URL}/api/v1/session/deploy`, {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ yaml }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          setError(data.error || "Deploy failed");
          return false;
        }
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Deploy failed");
        return false;
      } finally {
        setDeploying(false);
      }
    },
    [],
  );

  const reset = useCallback(() => {
    setState({
      step: "satellite-type",
      satelliteType: null,
      groundStationSet: null,
      constellation: null,
      protocol: null,
      extensions: [],
      areaStrategy: "flat",
    });
    setGeneratedYaml(null);
    setError(null);
  }, []);

  return {
    presets,
    rules,
    satelliteTypes,
    groundStationSets,
    availableStations,
    state,
    generating,
    deploying,
    generatedYaml,
    error,
    selectSatelliteType,
    selectGroundStationSet,
    selectCustomGroundStations,
    selectConstellation,
    selectProtocol,
    toggleExtension,
    setAreaStrategy,
    goToStep,
    goBack,
    goToReview,
    isExtensionAllowed,
    isExtensionEnabled,
    generate,
    deploy,
    reset,
  };
}
