// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** WebSocket hook — connects to VS-API and reads the state feed's frames. */

import { useEffect, useRef, useState, useCallback } from "react";
import { getWsUrl, fetchApiKey } from "../config";
import { mergeSnapshot } from "../snapshotMerge";
import type { SessionEphemeris } from "../builder/generated/builderApi";
import type { StateFeedFrame, StateSnapshot, StateSocketCommand } from "../generated/vsApi";

/** A state snapshot and when its frame arrived (performance.now() at decode). */
export interface ReceivedSnapshot {
  snapshot: StateSnapshot;
  arrivalMs: number;
}

interface WebSocketState {
  received: ReceivedSnapshot | null;
  ephemeris: SessionEphemeris | null;
  connected: boolean;
  hasEverConnected: boolean;
  sessionTransitioning: boolean;
  sessionError: string | null;
  switchDetail: string | null;
  /** Why the last token fetch failed while reconnecting; null once one succeeds. */
  tokenError: string | null;
  /** Why the last frame could not be read; null once one is read. */
  frameError: string | null;
  sendMessage: (command: StateSocketCommand) => void;
}

export function useWebSocket(): WebSocketState {
  const [received, setReceived] = useState<ReceivedSnapshot | null>(null);
  const [ephemeris, setEphemeris] = useState<SessionEphemeris | null>(null);
  const [connected, setConnected] = useState(false);
  const [hasEverConnected, setHasEverConnected] = useState(false);
  const [sessionTransitioning, setSessionTransitioning] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [switchDetail, setSwitchDetail] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
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
      // Stamp transport arrival here, not at React effect time — render
      // scheduling delay must not fold into the clock phase.
      const arrivalMs = performance.now();
      let frame: StateFeedFrame;
      try {
        frame = JSON.parse(event.data as string) as StateFeedFrame;
      } catch (err) {
        setFrameError(`VS-API sent a state frame that is not JSON: ${String(err)}`);
        return;
      }
      setFrameError(null);
      switch (frame.msg_type) {
        // Sent on connect and on epoch change.
        case "session_ephemeris":
          setEphemeris(frame);
          return;
        // Wiring progress from the Node Agent.
        case "wiring_progress":
          setReceived((prev) =>
            prev
              ? { ...prev, snapshot: { ...prev.snapshot, session_status_detail: frame.message } }
              : prev,
          );
          return;
        // VS-API's sequential switch flow. The first message clears state;
        // later ones carry progress detail from the Operator.
        case "session_transitioning":
          if (!sessionTransitioning) {
            setReceived(null);
            setEphemeris(null);
          }
          setSessionTransitioning(true);
          setSessionError(null);
          setSwitchDetail(frame.detail);
          return;
        case "session_ready":
          setSessionTransitioning(false);
          setSessionError(null);
          setSwitchDetail(null);
          setReceived({ snapshot: frame.snapshot, arrivalMs });
          return;
        case "session_failed":
          setSessionTransitioning(false);
          setSessionError(frame.error);
          setSwitchDetail(null);
          return;
        // The regular per-tick payload. ops_events merge against the previous
        // snapshot — the server ships them as deltas.
        case "state_snapshot":
          setReceived((prev) => ({
            snapshot: mergeSnapshot(prev?.snapshot ?? null, frame),
            arrivalMs,
          }));
          return;
        default: {
          const unknown: { msg_type: unknown } = frame;
          setFrameError(`VS-API sent a state frame the page does not know: ${String(unknown.msg_type)}`);
        }
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      // Only auto-retry after having connected at least once (VF spec Section 14).
      if (everConnectedRef.current) scheduleReconnect();
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  // Reconnect with a fresh token. A failed token fetch is shown and retried;
  // the stored key never stands in for it.
  const reconnectRef = useRef<() => void>(() => {});
  reconnectRef.current = () => {
    fetchApiKey().then(
      () => {
        setTokenError(null);
        connect();
      },
      (err: unknown) => {
        setTokenError(err instanceof Error ? err.message : String(err));
        scheduleReconnect();
      },
    );
  };

  const scheduleReconnect = useCallback(() => {
    const backoff = Math.min(1000 * Math.pow(2, retriesRef.current), 30000);
    retriesRef.current++;
    timerRef.current = setTimeout(() => reconnectRef.current(), backoff);
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  const sendMessage = useCallback((data: StateSocketCommand) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return {
    received,
    ephemeris,
    connected,
    hasEverConnected,
    sessionTransitioning,
    sessionError,
    switchDetail,
    tokenError,
    frameError,
    sendMessage,
  };
}
