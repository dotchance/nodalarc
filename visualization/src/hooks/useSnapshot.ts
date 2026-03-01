/** Snapshot state management — wraps useWebSocket + historical mode. */

import { useState, useCallback } from "react";
import { REST_URL } from "../config";
import { useWebSocket } from "./useWebSocket";
import type { StateSnapshot } from "../types";

interface SnapshotState {
  snapshot: StateSnapshot | null;
  connected: boolean;
  historicalMode: boolean;
  setHistoricalMode: (val: boolean) => void;
  fetchHistorical: (simTime: string) => Promise<void>;
}

export function useSnapshot(): SnapshotState {
  const { snapshot: liveSnapshot, connected } = useWebSocket();
  const [historicalMode, setHistoricalMode] = useState(false);
  const [historicalSnapshot, setHistoricalSnapshot] = useState<StateSnapshot | null>(null);

  const fetchHistorical = useCallback(async (simTime: string) => {
    try {
      const res = await fetch(`${REST_URL}/api/v1/state/${encodeURIComponent(simTime)}`);
      if (res.ok) {
        const data = (await res.json()) as StateSnapshot;
        setHistoricalSnapshot(data);
      }
    } catch {
      // Ignore fetch errors
    }
  }, []);

  return {
    snapshot: historicalMode ? historicalSnapshot : liveSnapshot,
    connected,
    historicalMode,
    setHistoricalMode,
    fetchHistorical,
  };
}
