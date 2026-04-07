// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Persistent interactive terminal (xterm.js + WebSocket-to-SSH proxy). */

import { useEffect, useRef, useState, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { REST_URL, getApiKey } from "../config";

interface PersistentTerminalProps {
  nodeId: string;
  fontSize: number;
}

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

export function PersistentTerminal({ nodeId, fontSize }: PersistentTerminalProps) {
  const termRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");

  // Connect to the VS-API terminal WebSocket
  const connect = useCallback(() => {
    if (!nodeId || !termRef.current) return;

    // Create terminal instance
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
      scrollback: 5000,
    });

    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();
    terminal.loadAddon(fitAddon);
    terminal.loadAddon(webLinksAddon);
    terminal.open(termRef.current);
    fitAddon.fit();

    terminalRef.current = terminal;
    fitRef.current = fitAddon;

    // Build WebSocket URL
    const wsBase = REST_URL.replace(/^http/, "ws");
    const key = getApiKey();
    const wsUrl = `${wsBase}/ws/v1/terminal/${encodeURIComponent(nodeId)}${
      key ? `?token=${encodeURIComponent(key)}` : ""
    }`;

    setStatus("connecting");
    terminal.writeln(`\x1b[90mConnecting to ${nodeId}, please wait...\x1b[0m`);

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    let receivedFirstOutput = false;

    ws.onopen = () => {
      // WebSocket is open but SSH session may still be negotiating.
      // Don't say "Connected" yet — wait for first output from vtysh.
      const dims = fitAddon.proposeDimensions();
      if (dims) {
        ws.send(JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows }));
      }
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "output" && msg.data) {
          if (!receivedFirstOutput) {
            receivedFirstOutput = true;
            setStatus("connected");
            // Clear the "Connecting" message before writing vtysh output
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
        setStatus("error");
        terminal.writeln("\r\n\x1b[31mAuthentication failed.\x1b[0m");
      } else if (event.code === 4404) {
        setStatus("error");
        terminal.writeln(`\r\n\x1b[31mNode ${nodeId} not found.\x1b[0m`);
      } else {
        setStatus("disconnected");
        terminal.writeln("\r\n\x1b[90mDisconnected.\x1b[0m");
      }
    };

    ws.onerror = () => {
      setStatus("error");
      terminal.writeln("\r\n\x1b[31mConnection error.\x1b[0m");
    };

    // Forward keyboard input to WebSocket
    terminal.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    // Handle terminal resize
    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
      const dims = fitAddon.proposeDimensions();
      if (dims && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows }));
      }
    });
    if (termRef.current) {
      resizeObserver.observe(termRef.current);
    }

    // Cleanup function
    return () => {
      resizeObserver.disconnect();
      ws.close();
      terminal.dispose();
      terminalRef.current = null;
      wsRef.current = null;
      fitRef.current = null;
    };
  }, [nodeId, fontSize]);

  // Connect on mount, reconnect on nodeId change
  useEffect(() => {
    const cleanup = connect();
    return cleanup;
  }, [connect]);

  // Update font size on live terminal
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.options.fontSize = fontSize;
      fitRef.current?.fit();
    }
  }, [fontSize]);

  const statusColor =
    status === "connected" ? "#44cc66" :
    status === "connecting" ? "#ffaa00" :
    status === "error" ? "#ff3333" : "#555577";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "2px 8px", fontSize: 10, color: "#888899",
        borderBottom: "1px solid #1a1a2e",
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: statusColor, display: "inline-block",
        }} />
        <span>{status === "connected" ? nodeId : status}</span>
      </div>
      <div
        ref={termRef}
        style={{ flex: 1, padding: "4px 0", overflow: "hidden" }}
      />
    </div>
  );
}
