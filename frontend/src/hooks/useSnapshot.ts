// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Snapshot state management — wraps useWebSocket + historical mode. */

import { useState, useCallback, useEffect, useRef } from "react";
import { REST_URL, authHeaders } from "../config";
import { useWebSocket, type ReceivedSnapshot } from "./useWebSocket";
import type { SessionEphemeris } from "../builder/generated/builderApi";
import type { StateSnapshot, StateSocketCommand } from "../generated/vsApi";
import { apiErrorFromException, apiErrorMessage } from "../ui/apiError";

interface SnapshotState {
  snapshot: StateSnapshot | null;
  /** When the shown snapshot arrived (performance.now()); null with no snapshot. */
  snapshotArrivalMs: number | null;
  ephemeris: SessionEphemeris | null;
  connected: boolean;
  hasEverConnected: boolean;
  sessionTransitioning: boolean;
  sessionError: string | null;
  switchDetail: string | null;
  tokenError: string | null;
  frameError: string | null;
  historicalMode: boolean;
  setHistoricalMode: (val: boolean) => void;
  fetchHistorical: (simTime: string) => Promise<boolean>;
  historicalError: string | null;
  sendMessage: (command: StateSocketCommand) => void;
}

export function useSnapshot(): SnapshotState {
  const {
    received: live,
    ephemeris,
    connected,
    hasEverConnected,
    sessionTransitioning,
    sessionError,
    switchDetail,
    tokenError,
    frameError,
    sendMessage,
  } = useWebSocket();
  const [historicalMode, setHistoricalModeState] = useState(false);
  const [historical, setHistorical] = useState<ReceivedSnapshot | null>(null);
  const [historicalError, setHistoricalError] = useState<string | null>(null);
  const historyAbortRef = useRef<AbortController | null>(null);
  const historyRequestSeqRef = useRef(0);

  const setHistoricalMode = useCallback((enabled: boolean) => {
    setHistoricalModeState(enabled);
    if (enabled && live) {
      setHistorical(live);
      setHistoricalError(null);
    }
  }, [live]);

  useEffect(() => {
    if (sessionTransitioning) {
      historyRequestSeqRef.current += 1;
      historyAbortRef.current?.abort();
      historyAbortRef.current = null;
      setHistoricalModeState(false);
      setHistorical(null);
      setHistoricalError(null);
    }
  }, [sessionTransitioning]);

  const fetchHistorical = useCallback(async (simTime: string): Promise<boolean> => {
    historyRequestSeqRef.current += 1;
    const requestSeq = historyRequestSeqRef.current;
    historyAbortRef.current?.abort();
    const controller = new AbortController();
    historyAbortRef.current = controller;

    const isCurrentRequest = () =>
      historyRequestSeqRef.current === requestSeq && historyAbortRef.current === controller;

    try {
      const res = await fetch(`${REST_URL}/api/v1/state/${encodeURIComponent(simTime)}`, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!res.ok) {
        const message = await apiErrorMessage(res);
        if (isCurrentRequest()) setHistoricalError(message);
        return false;
      }
      const snapshot = (await res.json()) as StateSnapshot;
      if (!isCurrentRequest()) return false;
      setHistorical({ snapshot, arrivalMs: performance.now() });
      setHistoricalError(null);
      return true;
    } catch (err) {
      if (controller.signal.aborted) return false;
      setHistoricalError(apiErrorFromException(err));
      return false;
    } finally {
      if (historyAbortRef.current === controller) historyAbortRef.current = null;
    }
  }, []);

  const shown = historicalMode ? historical : live;
  return {
    snapshot: shown?.snapshot ?? null,
    snapshotArrivalMs: shown?.arrivalMs ?? null,
    ephemeris,
    connected,
    hasEverConnected,
    sessionTransitioning,
    sessionError,
    switchDetail,
    tokenError,
    frameError,
    historicalMode,
    setHistoricalMode,
    fetchHistorical,
    historicalError,
    sendMessage,
  };
}
