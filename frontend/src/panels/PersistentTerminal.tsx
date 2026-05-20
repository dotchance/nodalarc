// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Multi-session persistent terminal — single renderer, multiple backends.
 *
 *  ONE xterm.js Terminal instance (single WebGL canvas) shared across all
 *  sessions. Each session is a WebSocket + output buffer. Switching tabs
 *  replays the target session's buffer into the shared terminal. This scales
 *  to many simultaneous sessions without GPU/memory pressure.
 */

import { useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { REST_URL, getApiKey } from "../config";

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

const MAX_BUFFER_LINES = 10_000;

/** Lightweight session — WebSocket + text buffer, no xterm.js instance. */
interface Session {
  nodeId: string;
  ws: WebSocket | null;
  status: ConnectionStatus;
  /** Accumulated output lines. Replayed into the shared terminal on tab switch. */
  buffer: string[];
  receivedFirstOutput: boolean;
}

interface PersistentTerminalProps {
  sessions: string[];
  activeNodeId: string | null;
  runtimeSessionId: string;
  onSessionStatusChange: (nodeId: string, status: ConnectionStatus) => void;
  fontSize: number;
}

export function PersistentTerminal({
  sessions,
  activeNodeId,
  runtimeSessionId,
  onSessionStatusChange,
  fontSize,
}: PersistentTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const sessionsRef = useRef<Map<string, Session>>(new Map());
  const activeRef = useRef<string | null>(null);
  const dataListenerRef = useRef<{ dispose: () => void } | null>(null);

  // Initialize the single shared terminal on mount
  useEffect(() => {
    if (!containerRef.current || terminalRef.current) return;

    const terminal = new Terminal({
      cursorBlink: true,
      fontSize,
      fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace",
      theme: {
        background: "#0d0d1a",
        foreground: "#e0e0e0",
        cursor: "#4488ff",
        selectionBackground: "#2a4a7a",
      },
      scrollback: MAX_BUFFER_LINES,
    });

    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.loadAddon(new WebLinksAddon());
    terminal.open(containerRef.current);
    fitAddon.fit();

    terminalRef.current = terminal;
    fitRef.current = fitAddon;

    return () => {
      terminal.dispose();
      terminalRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // Create a session (WebSocket + buffer) for a node
  const createSession = useCallback(
    (nodeId: string): Session => {
      const session: Session = {
        nodeId,
        ws: null,
        status: "connecting",
        buffer: [],
        receivedFirstOutput: false,
      };
      sessionsRef.current.set(nodeId, session);
      onSessionStatusChange(nodeId, "connecting");

      // Append to buffer + write to terminal if this is the active session
      const appendOutput = (text: string) => {
        session.buffer.push(text);
        // Trim buffer if too large
        if (session.buffer.length > MAX_BUFFER_LINES * 2) {
          session.buffer = session.buffer.slice(-MAX_BUFFER_LINES);
        }
        // Write to terminal only if this session is active
        if (activeRef.current === nodeId && terminalRef.current) {
          terminalRef.current.write(text);
        }
      };

      appendOutput(`\x1b[90mConnecting to ${nodeId}, please wait...\x1b[0m\r\n`);

      // Open WebSocket
      const wsBase = REST_URL.replace(/^http/, "ws");
      const key = getApiKey();
      const wsUrl = `${wsBase}/ws/v1/terminal/${encodeURIComponent(nodeId)}${
        key ? `?token=${encodeURIComponent(key)}` : ""
      }`;

      const ws = new WebSocket(wsUrl);
      session.ws = ws;

      ws.onopen = () => {
        // Send initial terminal size
        if (fitRef.current) {
          const dims = fitRef.current.proposeDimensions();
          if (dims) {
            ws.send(JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows }));
          }
        }
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "output" && msg.data) {
            if (!session.receivedFirstOutput) {
              session.receivedFirstOutput = true;
              session.status = "connected";
              onSessionStatusChange(nodeId, "connected");
              // Clear the "Connecting" message from the buffer
              session.buffer = [];
              if (activeRef.current === nodeId && terminalRef.current) {
                terminalRef.current.clear();
              }
            }
            appendOutput(msg.data);
          }
        } catch {
          appendOutput(event.data);
        }
      };

      ws.onclose = (event) => {
        if (event.code === 4401) {
          session.status = "error";
          onSessionStatusChange(nodeId, "error");
          appendOutput("\r\n\x1b[31mAuthentication failed.\x1b[0m");
        } else if (event.code === 4404) {
          session.status = "error";
          onSessionStatusChange(nodeId, "error");
          appendOutput(`\r\n\x1b[31mNode ${nodeId} not found.\x1b[0m`);
        } else {
          session.status = "disconnected";
          onSessionStatusChange(nodeId, "disconnected");
          appendOutput("\r\n\x1b[90mDisconnected.\x1b[0m");
        }
      };

      ws.onerror = () => {
        session.status = "error";
        onSessionStatusChange(nodeId, "error");
        appendOutput("\r\n\x1b[31mConnection error.\x1b[0m");
      };

      return session;
    },
    [onSessionStatusChange]
  );

  // Runtime session identity changes even when router node IDs are reused.
  // Tear down stale WebSockets so a wizard redeploy opens terminals into the
  // current pod set instead of replaying dead sessions from the previous run.
  useEffect(() => {
    for (const session of sessionsRef.current.values()) {
      session.ws?.close();
    }
    sessionsRef.current.clear();
    terminalRef.current?.reset();
  }, [runtimeSessionId]);

  // Manage session lifecycle
  useEffect(() => {
    const currentIds = new Set(sessions);

    // Create sessions for new nodes
    for (const nodeId of sessions) {
      if (!sessionsRef.current.has(nodeId)) {
        createSession(nodeId);
      }
    }

    // Clean up removed sessions
    for (const [nodeId, session] of sessionsRef.current.entries()) {
      if (!currentIds.has(nodeId)) {
        session.ws?.close();
        sessionsRef.current.delete(nodeId);
      }
    }
  }, [sessions, createSession, runtimeSessionId]);

  // Switch active session — replay buffer into shared terminal
  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;

    // Detach keyboard listener from previous session
    if (dataListenerRef.current) {
      dataListenerRef.current.dispose();
      dataListenerRef.current = null;
    }

    activeRef.current = activeNodeId;

    if (!activeNodeId) {
      terminal.clear();
      terminal.write("\x1b[90mNo session selected.\x1b[0m");
      return;
    }

    const session = sessionsRef.current.get(activeNodeId);
    if (!session) return;

    // Clear terminal and replay this session's buffer
    terminal.reset();
    for (const chunk of session.buffer) {
      terminal.write(chunk);
    }
    terminal.scrollToBottom();

    // Attach keyboard listener to this session's WebSocket
    dataListenerRef.current = terminal.onData((data) => {
      if (session.ws && session.ws.readyState === WebSocket.OPEN) {
        session.ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    // Focus and fit
    requestAnimationFrame(() => {
      fitRef.current?.fit();
      terminal.focus();
      // Send resize to server
      if (fitRef.current && session.ws?.readyState === WebSocket.OPEN) {
        const dims = fitRef.current.proposeDimensions();
        if (dims) {
          session.ws.send(JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows }));
        }
      }
    });
  }, [activeNodeId]);

  // Handle container resize
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(() => {
      fitRef.current?.fit();
      const session = activeRef.current
        ? sessionsRef.current.get(activeRef.current)
        : null;
      if (session?.ws?.readyState === WebSocket.OPEN && fitRef.current) {
        const dims = fitRef.current.proposeDimensions();
        if (dims) {
          session.ws.send(JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows }));
        }
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Update font size
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.options.fontSize = fontSize;
      fitRef.current?.fit();
    }
  }, [fontSize]);

  // Cleanup all sessions on unmount
  useEffect(() => {
    return () => {
      for (const session of sessionsRef.current.values()) {
        session.ws?.close();
      }
      sessionsRef.current.clear();
      dataListenerRef.current?.dispose();
    };
  }, []);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: "100%", overflow: "hidden" }}
    />
  );
}
