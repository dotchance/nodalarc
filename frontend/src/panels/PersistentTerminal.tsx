// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Multi-session persistent terminal manager with tab switching.
 *
 *  Each node gets its own SSH session that stays alive when switching tabs.
 *  Sessions accumulate output in the background — switching back shows
 *  the full scroll buffer and cursor where you left it.
 */

import { useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { REST_URL, getApiKey } from "../config";

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

interface SessionState {
  nodeId: string;
  terminal: Terminal;
  fitAddon: FitAddon;
  ws: WebSocket | null;
  status: ConnectionStatus;
  containerEl: HTMLDivElement;
}

interface PersistentTerminalProps {
  sessions: string[];             // ordered list of open node IDs
  activeNodeId: string | null;    // which tab is visible
  onSessionStatusChange: (nodeId: string, status: ConnectionStatus) => void;
  fontSize: number;
}

export function PersistentTerminal({
  sessions,
  activeNodeId,
  onSessionStatusChange,
  fontSize,
}: PersistentTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sessionsRef = useRef<Map<string, SessionState>>(new Map());

  // Create or retrieve a session for a node
  const getOrCreateSession = useCallback(
    (nodeId: string): SessionState => {
      const existing = sessionsRef.current.get(nodeId);
      if (existing) return existing;

      // Create terminal instance
      const terminal = new Terminal({
        cursorBlink: true,
        fontSize,
        fontFamily:
          "'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace",
        theme: {
          background: "#0d0d1a",
          foreground: "#e0e0e0",
          cursor: "#4488ff",
          selectionBackground: "#2a4a7a",
        },
        scrollback: 10000,
      });

      const fitAddon = new FitAddon();
      const webLinksAddon = new WebLinksAddon();
      terminal.loadAddon(fitAddon);
      terminal.loadAddon(webLinksAddon);

      // Create a container div for this terminal (hidden until active)
      const containerEl = document.createElement("div");
      containerEl.style.cssText =
        "width:100%;height:100%;display:none;padding:4px 0;";

      const session: SessionState = {
        nodeId,
        terminal,
        fitAddon,
        ws: null,
        status: "connecting",
        containerEl,
      };
      sessionsRef.current.set(nodeId, session);

      // Append container and open terminal
      if (containerRef.current) {
        containerRef.current.appendChild(containerEl);
      }
      terminal.open(containerEl);

      // Connect WebSocket
      terminal.writeln(`\x1b[90mConnecting to ${nodeId}, please wait...\x1b[0m`);
      onSessionStatusChange(nodeId, "connecting");

      const wsBase = REST_URL.replace(/^http/, "ws");
      const key = getApiKey();
      const wsUrl = `${wsBase}/ws/v1/terminal/${encodeURIComponent(nodeId)}${
        key ? `?token=${encodeURIComponent(key)}` : ""
      }`;

      const ws = new WebSocket(wsUrl);
      session.ws = ws;
      let receivedFirstOutput = false;

      ws.onopen = () => {
        const dims = fitAddon.proposeDimensions();
        if (dims) {
          ws.send(
            JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows })
          );
        }
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "output" && msg.data) {
            if (!receivedFirstOutput) {
              receivedFirstOutput = true;
              session.status = "connected";
              onSessionStatusChange(nodeId, "connected");
              terminal.write("\x1b[2K\x1b[1A\x1b[2K\r");
            }
            terminal.write(msg.data);
          }
        } catch {
          terminal.write(event.data);
        }
      };

      ws.onclose = (event) => {
        if (event.code === 4401) {
          session.status = "error";
          onSessionStatusChange(nodeId, "error");
          terminal.writeln("\r\n\x1b[31mAuthentication failed.\x1b[0m");
        } else if (event.code === 4404) {
          session.status = "error";
          onSessionStatusChange(nodeId, "error");
          terminal.writeln(`\r\n\x1b[31mNode ${nodeId} not found.\x1b[0m`);
        } else {
          session.status = "disconnected";
          onSessionStatusChange(nodeId, "disconnected");
          terminal.writeln("\r\n\x1b[90mDisconnected.\x1b[0m");
        }
      };

      ws.onerror = () => {
        session.status = "error";
        onSessionStatusChange(nodeId, "error");
        terminal.writeln("\r\n\x1b[31mConnection error.\x1b[0m");
      };

      terminal.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "input", data }));
        }
      });

      return session;
    },
    [fontSize, onSessionStatusChange]
  );

  // Manage session lifecycle — create new sessions, clean up removed ones
  useEffect(() => {
    const currentIds = new Set(sessions);

    // Create sessions for new nodes
    for (const nodeId of sessions) {
      getOrCreateSession(nodeId);
    }

    // Clean up sessions that were removed
    for (const [nodeId, session] of sessionsRef.current.entries()) {
      if (!currentIds.has(nodeId)) {
        session.ws?.close();
        session.terminal.dispose();
        session.containerEl.remove();
        sessionsRef.current.delete(nodeId);
      }
    }
  }, [sessions, getOrCreateSession]);

  // Show/hide terminal containers based on active tab
  useEffect(() => {
    for (const [nodeId, session] of sessionsRef.current.entries()) {
      const isActive = nodeId === activeNodeId;
      session.containerEl.style.display = isActive ? "block" : "none";
      if (isActive) {
        // Fit to container and focus
        requestAnimationFrame(() => {
          session.fitAddon.fit();
          session.terminal.focus();
          // Send resize to server
          const dims = session.fitAddon.proposeDimensions();
          if (
            dims &&
            session.ws &&
            session.ws.readyState === WebSocket.OPEN
          ) {
            session.ws.send(
              JSON.stringify({
                type: "resize",
                cols: dims.cols,
                rows: dims.rows,
              })
            );
          }
        });
      }
    }
  }, [activeNodeId]);

  // Handle resize of the container
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(() => {
      if (!activeNodeId) return;
      const session = sessionsRef.current.get(activeNodeId);
      if (session) {
        session.fitAddon.fit();
        const dims = session.fitAddon.proposeDimensions();
        if (
          dims &&
          session.ws &&
          session.ws.readyState === WebSocket.OPEN
        ) {
          session.ws.send(
            JSON.stringify({
              type: "resize",
              cols: dims.cols,
              rows: dims.rows,
            })
          );
        }
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [activeNodeId]);

  // Update font size on all terminals
  useEffect(() => {
    for (const session of sessionsRef.current.values()) {
      session.terminal.options.fontSize = fontSize;
      session.fitAddon.fit();
    }
  }, [fontSize]);

  // Cleanup all sessions on unmount
  useEffect(() => {
    return () => {
      for (const session of sessionsRef.current.values()) {
        session.ws?.close();
        session.terminal.dispose();
        session.containerEl.remove();
      }
      sessionsRef.current.clear();
    };
  }, []);

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%", overflow: "hidden" }} />
  );
}
