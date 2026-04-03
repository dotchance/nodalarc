// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Snapshot state management — wraps useWebSocket + historical mode. */

import { useState, useCallback } from "react";
import { REST_URL, authHeaders } from "../config";
import { useWebSocket } from "./useWebSocket";
import type { StateSnapshot } from "../types";

interface SnapshotState {
  snapshot: StateSnapshot | null;
  connected: boolean;
  hasEverConnected: boolean;
  kicked: boolean;
  historicalMode: boolean;
  setHistoricalMode: (val: boolean) => void;
  fetchHistorical: (simTime: string) => Promise<void>;
}

export function useSnapshot(): SnapshotState {
  const { snapshot: liveSnapshot, connected, hasEverConnected, kicked } = useWebSocket();
  const [historicalMode, setHistoricalMode] = useState(false);
  const [historicalSnapshot, setHistoricalSnapshot] = useState<StateSnapshot | null>(null);

  const fetchHistorical = useCallback(async (simTime: string) => {
    try {
      const res = await fetch(`${REST_URL}/api/v1/state/${encodeURIComponent(simTime)}`, {
        headers: authHeaders(),
      });
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
    hasEverConnected,
    kicked,
    historicalMode,
    setHistoricalMode,
    fetchHistorical,
  };
}
