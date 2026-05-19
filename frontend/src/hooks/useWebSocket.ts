// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** WebSocket hook — connects to VS-API, parses StateSnapshot and SessionEphemeris. */

import { useEffect, useRef, useState, useCallback } from "react";
import { getWsUrl, fetchApiKey } from "../config";
import type { StateSnapshot } from "../types";
import type { SessionEphemeris, PlaybackStateMsg } from "../sim/ephemeris";

interface WebSocketState {
  snapshot: StateSnapshot | null;
  ephemeris: SessionEphemeris | null;
  playbackState: PlaybackStateMsg | null;
  connected: boolean;
  hasEverConnected: boolean;
  kicked: boolean;
  sessionTransitioning: boolean;
  sessionError: string | null;
  switchDetail: string | null;
  sendMessage: (data: Record<string, unknown>) => void;
}

export function useWebSocket(): WebSocketState {
  const [snapshot, setSnapshot] = useState<StateSnapshot | null>(null);
  const [ephemeris, setEphemeris] = useState<SessionEphemeris | null>(null);
  const [playbackState, setPlaybackState] =
    useState<PlaybackStateMsg | null>(null);
  const [connected, setConnected] = useState(false);
  const [hasEverConnected, setHasEverConnected] = useState(false);
  const [kicked, setKicked] = useState(false);
  const [sessionTransitioning, setSessionTransitioning] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [switchDetail, setSwitchDetail] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const everConnectedRef = useRef(false);

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setHasEverConnected(true);
      everConnectedRef.current = true;
      retriesRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string);

        // SessionEphemeris — sent on connect and on epoch change
        if (data.msg_type === "session_ephemeris") {
          setEphemeris(data as SessionEphemeris);
          return;
        }

        // Wiring progress — instant update from Node Agent via NATS
        if (data.msg_type === "wiring_progress") {
          setSnapshot((prev) =>
            prev ? { ...prev, session_status_detail: data.message } : prev,
          );
          return;
        }

        // Session lifecycle — VS-API sequential switch flow.
        // The first message clears state. Subsequent messages carry
        // progress detail from the Operator (pod counts, wiring status).
        if (data.msg_type === "session_transitioning") {
          if (!sessionTransitioning) {
            setSnapshot(null);
            setEphemeris(null);
          }
          setSessionTransitioning(true);
          setSessionError(null);
          if (data.detail) {
            setSwitchDetail(data.detail as string);
          }
          return;
        }
        if (data.msg_type === "session_ready") {
          setSessionTransitioning(false);
          setSessionError(null);
          setSwitchDetail(null);
          if (data.snapshot) {
            setSnapshot(data.snapshot as StateSnapshot);
          }
          return;
        }
        if (data.msg_type === "session_failed") {
          setSessionTransitioning(false);
          setSessionError(data.error || "Session switch failed");
          setSwitchDetail(null);
          return;
        }

        // PlaybackState — sent on state transitions
        if (
          data.state &&
          data.epoch_id !== undefined &&
          !data.sim_time &&
          !data.schema_version
        ) {
          setPlaybackState(data as PlaybackStateMsg);
          return;
        }

        // StateSnapshot — the regular per-tick payload
        setSnapshot(data as StateSnapshot);
      } catch {
        // Drop malformed frames
      }
    };

    ws.onclose = (ev) => {
      setConnected(false);
      wsRef.current = null;
      // Kicked by another browser — stop reconnecting
      if (ev.code === 4409) {
        setKicked(true);
        return;
      }
      // Only auto-retry after having connected at least once (VF spec Section 14).
      if (everConnectedRef.current) {
        if (ev.code === 4401 || ev.code === 4003 || ev.code === 1008) {
          retriesRef.current = 0;
        }
        fetchApiKey().finally(() => scheduleReconnect());
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  const scheduleReconnect = useCallback(() => {
    const backoff = Math.min(1000 * Math.pow(2, retriesRef.current), 30000);
    retriesRef.current++;
    timerRef.current = setTimeout(connect, backoff);
  }, [connect]);

  useEffect(() => {
    connect();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  const sendMessage = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return {
    snapshot,
    ephemeris,
    playbackState,
    connected,
    hasEverConnected,
    kicked,
    sessionTransitioning,
    sessionError,
    switchDetail,
    sendMessage,
  };
}
