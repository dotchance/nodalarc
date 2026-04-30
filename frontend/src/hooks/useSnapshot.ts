// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Snapshot state management — wraps useWebSocket + historical mode. */

import { useState, useCallback } from "react";
import { REST_URL, authHeaders } from "../config";
import { useWebSocket } from "./useWebSocket";
import type { StateSnapshot } from "../types";
import type { SessionEphemeris, PlaybackStateMsg } from "../sim/ephemeris";

interface SnapshotState {
  snapshot: StateSnapshot | null;
  ephemeris: SessionEphemeris | null;
  playbackState: PlaybackStateMsg | null;
  connected: boolean;
  hasEverConnected: boolean;
  kicked: boolean;
  sessionTransitioning: boolean;
  sessionError: string | null;
  historicalMode: boolean;
  setHistoricalMode: (val: boolean) => void;
  fetchHistorical: (simTime: string) => Promise<void>;
  sendMessage: (data: Record<string, unknown>) => void;
}

export function useSnapshot(): SnapshotState {
  const {
    snapshot: liveSnapshot,
    ephemeris,
    playbackState,
    connected,
    hasEverConnected,
    kicked,
    sessionTransitioning,
    sessionError,
    sendMessage,
  } = useWebSocket();
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
    ephemeris,
    playbackState,
    connected,
    hasEverConnected,
    kicked,
    sessionTransitioning,
    sessionError,
    historicalMode,
    setHistoricalMode,
    fetchHistorical,
    sendMessage,
  };
}
