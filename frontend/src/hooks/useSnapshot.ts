// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Snapshot state management — wraps useWebSocket + historical mode. */

import { useState, useCallback, useEffect } from "react";
import { REST_URL, authHeaders } from "../config";
import { useWebSocket } from "./useWebSocket";
import type { StateSnapshot } from "../types";
import type { SessionEphemeris, PlaybackStateMsg } from "../sim/ephemeris";


function isStateSnapshot(value: unknown): value is StateSnapshot {
  if (value === null || typeof value !== "object") return false;
  const v = value as Partial<StateSnapshot>;
  return (
    typeof v.sim_time === "string" &&
    typeof v.wall_time === "string" &&
    Array.isArray(v.nodes) &&
    Array.isArray(v.links) &&
    Array.isArray(v.traced_paths) &&
    Array.isArray(v.active_flows) &&
    Array.isArray(v.recent_events)
  );
}

interface SnapshotState {
  snapshot: StateSnapshot | null;
  ephemeris: SessionEphemeris | null;
  playbackState: PlaybackStateMsg | null;
  connected: boolean;
  hasEverConnected: boolean;
  kicked: boolean;
  sessionTransitioning: boolean;
  sessionError: string | null;
  switchDetail: string | null;
  historicalMode: boolean;
  setHistoricalMode: (val: boolean) => void;
  fetchHistorical: (simTime: string) => Promise<boolean>;
  historicalError: string | null;
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
    switchDetail,
    sendMessage,
  } = useWebSocket();
  const [historicalMode, setHistoricalModeState] = useState(false);
  const [historicalSnapshot, setHistoricalSnapshot] = useState<StateSnapshot | null>(null);
  const [historicalError, setHistoricalError] = useState<string | null>(null);

  const setHistoricalMode = useCallback((enabled: boolean) => {
    setHistoricalModeState(enabled);
    if (enabled && liveSnapshot) {
      setHistoricalSnapshot(liveSnapshot);
      setHistoricalError(null);
    }
  }, [liveSnapshot]);

  useEffect(() => {
    if (sessionTransitioning) {
      setHistoricalModeState(false);
      setHistoricalSnapshot(null);
      setHistoricalError(null);
    }
  }, [sessionTransitioning]);

  const fetchHistorical = useCallback(async (simTime: string): Promise<boolean> => {
    try {
      const res = await fetch(`${REST_URL}/api/v1/state/${encodeURIComponent(simTime)}`, {
        headers: authHeaders(),
      });
      const data = await res.json().catch(() => null);
      if (res.ok && isStateSnapshot(data)) {
        setHistoricalSnapshot(data);
        setHistoricalError(null);
        return true;
      }
      const message =
        data && typeof data === "object" && "error" in data && typeof data.error === "string"
          ? data.error
          : `Historical state unavailable (${res.status})`;
      setHistoricalError(message);
      return false;
    } catch {
      setHistoricalError("Historical state unavailable");
      return false;
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
    switchDetail,
    historicalMode,
    setHistoricalMode,
    fetchHistorical,
    historicalError,
    sendMessage,
  };
}
