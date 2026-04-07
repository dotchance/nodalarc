// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Bottom CLI drawer — commands mode (one-shot) + terminal mode (persistent SSH). */

import { useState, useRef, useEffect, useCallback } from "react";
import { useIntrospect } from "../hooks/useIntrospect";
import { PersistentTerminal } from "./PersistentTerminal";
import { REST_URL, authHeaders } from "../config";
import type { StateSnapshot, Selection } from "../types";

type CliMode = "commands" | "terminal";

interface CliDrawerProps {
  open: boolean;
  onClose: () => void;
  snapshot: StateSnapshot | null;
  selection: Selection | null;
}

const MIN_HEIGHT = 120;
const MAX_HEIGHT_PCT = 0.6;
const DEFAULT_HEIGHT = 300;

export function CliDrawer({ open, onClose, snapshot, selection }: CliDrawerProps) {
  const { loading, output, error, commands, execute } = useIntrospect();

  const [mode, setMode] = useState<CliMode>("terminal");
  const [fontSize, setFontSize] = useState(12);
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [selectedNode, setSelectedNode] = useState("");
  const [selectedCommand, setSelectedCommand] = useState("");
  const draggingRef = useRef(false);
  const drawerRef = useRef<HTMLDivElement>(null);

  // Auto-select node when selection changes
  useEffect(() => {
    if (selection && selection.type !== "link") {
      setSelectedNode(selection.id);
    }
  }, [selection]);

  // Default to first command when commands load
  useEffect(() => {
    if (commands.length > 0 && !selectedCommand) {
      setSelectedCommand(commands[0] ?? "");
    }
  }, [commands, selectedCommand]);

  // Drag handle for resizing
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    draggingRef.current = true;
    e.preventDefault();
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const viewport = drawerRef.current?.parentElement;
      if (!viewport) return;
      const rect = viewport.getBoundingClientRect();
      const maxH = rect.height * MAX_HEIGHT_PCT;
      const newH = rect.bottom - e.clientY;
      setHeight(Math.max(MIN_HEIGHT, Math.min(maxH, newH)));
    };
    const onUp = () => { draggingRef.current = false; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const handleRun = useCallback(() => {
    if (selectedNode && selectedCommand) {
      execute(selectedNode, selectedCommand);
    }
  }, [selectedNode, selectedCommand, execute]);

  const handleDownloadConfig = useCallback(async () => {
    if (!selectedNode) return;
    try {
      const resp = await fetch(
        `${REST_URL}/api/v1/nodes/${encodeURIComponent(selectedNode)}/config`,
        { headers: authHeaders() },
      );
      if (resp.ok) {
        const text = await resp.text();
        const blob = new Blob([text], { type: "text/plain" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${selectedNode}.conf`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch { /* ignore */ }
  }, [selectedNode]);

  const nodes = snapshot
    ? [...snapshot.nodes].sort((a, b) => a.node_id.localeCompare(b.node_id))
    : [];

  if (!open) return null;

  return (
    <div
      ref={drawerRef}
      style={{
        position: "absolute", bottom: 0, left: 0, right: 0, zIndex: 15,
        height, background: "rgba(26,26,46,0.96)", backdropFilter: "blur(4px)",
        borderTop: "1px solid #2a2a4e", display: "flex", flexDirection: "column",
      }}
    >
      {/* Drag handle */}
      <div
        onMouseDown={handleDragStart}
        style={{
          flexShrink: 0, height: 5, cursor: "ns-resize",
          background: "#2a2a4e",
        }}
      />

      {/* Toolbar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "6px 12px", flexShrink: 0, borderBottom: "1px solid #2a2a4e",
      }}>
        {/* Mode toggle */}
        <div style={{
          display: "flex", borderRadius: 4, overflow: "hidden",
          border: "1px solid #2a2a4e",
        }}>
          <button
            onClick={() => setMode("terminal")}
            style={{
              background: mode === "terminal" ? "#4488ff" : "#0d0d1a",
              color: mode === "terminal" ? "#0d0d1a" : "#888899",
              border: "none", fontSize: 11, padding: "3px 10px", cursor: "pointer",
              fontWeight: mode === "terminal" ? 600 : 400,
            }}
          >Terminal</button>
          <button
            onClick={() => setMode("commands")}
            style={{
              background: mode === "commands" ? "#4488ff" : "#0d0d1a",
              color: mode === "commands" ? "#0d0d1a" : "#888899",
              border: "none", borderLeft: "1px solid #2a2a4e",
              fontSize: 11, padding: "3px 10px", cursor: "pointer",
              fontWeight: mode === "commands" ? 600 : 400,
            }}
          >Commands</button>
        </div>

        {/* Node selector */}
        <label style={{ fontSize: 11, color: "#888899", whiteSpace: "nowrap" }}>Node:</label>
        <select
          value={selectedNode}
          onChange={(e) => setSelectedNode(e.target.value)}
          style={{
            background: "#0d0d1a", color: "#e0e0e0", border: "1px solid #2a2a4e",
            borderRadius: 4, padding: "4px 8px", fontSize: 12, maxWidth: 200,
          }}
        >
          <option value="">—</option>
          {nodes.map((n) => (
            <option key={n.node_id} value={n.node_id}>{n.node_id}</option>
          ))}
        </select>

        {/* Commands mode controls */}
        {mode === "commands" && (
          <>
            <label style={{ fontSize: 11, color: "#888899", whiteSpace: "nowrap" }}>Command:</label>
            <select
              value={selectedCommand}
              onChange={(e) => setSelectedCommand(e.target.value)}
              style={{
                background: "#0d0d1a", color: "#e0e0e0", border: "1px solid #2a2a4e",
                borderRadius: 4, padding: "4px 8px", fontSize: 12, maxWidth: 200,
              }}
            >
              {commands.map((cmd) => (
                <option key={cmd} value={cmd}>{cmd}</option>
              ))}
            </select>
            <button
              onClick={handleRun}
              disabled={loading || !selectedNode || !selectedCommand}
              style={{
                background: "#4488ff", color: "#0d0d1a", border: "none", borderRadius: 4,
                padding: "4px 14px", fontSize: 12, fontWeight: 600, cursor: "pointer",
                opacity: (loading || !selectedNode || !selectedCommand) ? 0.4 : 1,
              }}
            >
              {loading ? "Running..." : "Run"}
            </button>
          </>
        )}

        {/* Download config button */}
        {selectedNode && (
          <button
            onClick={handleDownloadConfig}
            title="Download running config"
            style={{
              background: "none", border: "1px solid #2a2a4e", borderRadius: 4,
              color: "#888899", fontSize: 11, cursor: "pointer", padding: "3px 8px",
            }}
          >⬇ Config</button>
        )}

        {/* Right side controls */}
        <span style={{
          marginLeft: "auto", display: "flex", alignItems: "center", gap: 0,
          borderLeft: "1px solid #2a2a4e", paddingLeft: 8,
        }}>
          <button
            onClick={() => setFontSize((s) => Math.max(9, s - 1))}
            title="Decrease font size"
            style={{
              background: "none", border: "1px solid #2a2a4e", borderRadius: "4px 0 0 4px",
              color: "#888899", fontSize: 11, cursor: "pointer", padding: "2px 6px",
            }}
          >A-</button>
          <button
            onClick={() => setFontSize((s) => Math.min(18, s + 1))}
            title="Increase font size"
            style={{
              background: "none", border: "1px solid #2a2a4e", borderLeft: "none",
              borderRadius: "0 4px 4px 0",
              color: "#888899", fontSize: 13, cursor: "pointer", padding: "2px 6px",
            }}
          >A+</button>
        </span>

        <button
          onClick={onClose}
          title="Close CLI drawer"
          style={{
            background: "none", border: "none",
            color: "#555577", fontSize: 16, cursor: "pointer", padding: "2px 6px",
          }}
        >✕</button>
      </div>

      {/* Content area */}
      <div style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
        {mode === "terminal" && selectedNode ? (
          <PersistentTerminal nodeId={selectedNode} fontSize={fontSize} />
        ) : mode === "terminal" && !selectedNode ? (
          <div style={{ padding: "12px", color: "#555577", fontStyle: "italic", fontSize: 12 }}>
            Select a node to open an interactive terminal.
          </div>
        ) : (
          /* Commands mode output */
          <div style={{ padding: "8px 12px", overflow: "auto", height: "100%" }}>
            {loading && (
              <pre style={{ margin: 0, fontFamily: "monospace", fontSize, color: "#ffaa00" }}>
                Running command...
              </pre>
            )}
            {error && (
              <pre style={{ margin: 0, fontFamily: "monospace", fontSize, color: "#ff3333" }}>
                {error}
              </pre>
            )}
            {output && (
              <pre style={{ margin: 0, fontFamily: "monospace", fontSize, color: "#e0e0e0", whiteSpace: "pre" }}>
                {output}
              </pre>
            )}
            {!loading && !error && !output && (
              <pre style={{ margin: 0, fontFamily: "monospace", fontSize, color: "#555577", fontStyle: "italic" }}>
                Select a node and command, then click Run.
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
