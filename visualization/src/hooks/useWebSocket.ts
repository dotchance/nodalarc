/** WebSocket hook — connects to VS-API, parses StateSnapshot, drops intermediate frames. */

import { useEffect, useRef, useState, useCallback } from "react";
import { WS_URL } from "../config";
import type { StateSnapshot } from "../types";

interface WebSocketState {
  snapshot: StateSnapshot | null;
  connected: boolean;
  hasEverConnected: boolean;
}

export function useWebSocket(): WebSocketState {
  const [snapshot, setSnapshot] = useState<StateSnapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const [hasEverConnected, setHasEverConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setHasEverConnected(true);
      retriesRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string) as StateSnapshot;
        setSnapshot(data);
      } catch {
        // Drop malformed frames
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      scheduleReconnect();
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

  return { snapshot, connected, hasEverConnected };
}
