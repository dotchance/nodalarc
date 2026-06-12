// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Bottom CLI drawer — commands mode (one-shot) + terminal mode (multi-session SSH). */

import { useState, useRef, useEffect, useCallback } from "react";
import { useIntrospect } from "../hooks/useIntrospect";
import { PersistentTerminal } from "./PersistentTerminal";
import { REST_URL, authHeaders } from "../config";
import { Button, IconButton } from "../ui/Button";
import { Tabs, type TabItem } from "../ui/Tabs";
import type { StatusTone } from "../ui/Badge";
import type { StateSnapshot, Selection } from "../types";

type CliMode = "commands" | "terminal";
type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

interface CliDrawerProps {
  open: boolean;
  onClose: () => void;
  snapshot: StateSnapshot | null;
  selection: Selection | null;
}

const MIN_HEIGHT = 120;
const MAX_HEIGHT_PCT = 0.6;
const DEFAULT_HEIGHT = 300;

function statusTone(status?: ConnectionStatus): StatusTone {
  if (status === "connected") return "ok";
  if (status === "connecting") return "warn";
  if (status === "error") return "fail";
  return "neutral";
}

export function CliDrawer({ open, onClose, snapshot, selection }: CliDrawerProps) {
  const { loading, output, error, commands, execute } = useIntrospect();

  const [mode, setMode] = useState<CliMode>("terminal");
  const [fontSize, setFontSize] = useState(12);
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [selectedNode, setSelectedNode] = useState("");
  const [selectedCommand, setSelectedCommand] = useState("");
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const draggingRef = useRef(false);
  const drawerRef = useRef<HTMLDivElement>(null);

  // Multi-session terminal state
  const [openSessions, setOpenSessions] = useState<string[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [sessionStatuses, setSessionStatuses] = useState<Map<string, ConnectionStatus>>(new Map());
  const runtimeSessionId = snapshot?.session_id ?? "";
  const previousRuntimeSessionIdRef = useRef("");

  useEffect(() => {
    if (selection && selection.type !== "link") {
      setSelectedNode(selection.id);
    }
  }, [selection]);

  useEffect(() => {
    if (commands.length > 0 && !selectedCommand) {
      setSelectedCommand(commands[0] ?? "");
    }
  }, [commands, selectedCommand]);

  const openTerminalSession = useCallback((nodeId: string) => {
    if (!nodeId) return;
    setOpenSessions((prev) => {
      if (prev.includes(nodeId)) {
        setActiveSession(nodeId);
        return prev;
      }
      setActiveSession(nodeId);
      return [...prev, nodeId];
    });
  }, []);

  useEffect(() => {
    if (mode === "terminal" && selectedNode) {
      openTerminalSession(selectedNode);
    }
  }, [selectedNode, mode, openTerminalSession]);

  const closeSession = useCallback((nodeId: string) => {
    setOpenSessions((prev) => {
      const next = prev.filter((id) => id !== nodeId);
      setActiveSession((current) => (current === nodeId ? next[next.length - 1] ?? null : current));
      return next;
    });
    setSessionStatuses((prev) => {
      const next = new Map(prev);
      next.delete(nodeId);
      return next;
    });
  }, []);

  const handleSessionStatus = useCallback((nodeId: string, status: ConnectionStatus) => {
    setSessionStatuses((prev) => {
      const next = new Map(prev);
      next.set(nodeId, status);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!runtimeSessionId) return;
    if (
      previousRuntimeSessionIdRef.current &&
      previousRuntimeSessionIdRef.current !== runtimeSessionId
    ) {
      setSessionStatuses(new Map());
    }
    previousRuntimeSessionIdRef.current = runtimeSessionId;
  }, [runtimeSessionId]);

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
    const target = mode === "terminal" ? activeSession : selectedNode;
    if (!target) return;
    setDownloadError(null);
    try {
      const resp = await fetch(
        `${REST_URL}/api/v1/nodes/${encodeURIComponent(target)}/config`,
        { headers: authHeaders() },
      );
      if (!resp.ok) {
        setDownloadError(`config download failed: HTTP ${resp.status}`);
        return;
      }
      const text = await resp.text();
      const blob = new Blob([text], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${target}.conf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setDownloadError("config download failed: network error");
    }
  }, [mode, activeSession, selectedNode]);

  const nodes = snapshot
    ? [...snapshot.nodes].sort((a, b) => a.node_id.localeCompare(b.node_id))
    : [];

  if (!open) return null;

  const sessionTabs: TabItem[] = openSessions.map((nodeId) => ({
    key: nodeId,
    label: nodeId,
    closable: true,
    tone: statusTone(sessionStatuses.get(nodeId)),
  }));

  return (
    <div ref={drawerRef} className="cli-drawer" style={{ height }}>
      <div className="cli-drag-handle" onMouseDown={handleDragStart} />

      <div className="cli-toolbar">
        <span className="cli-mode-toggle">
          <Button active={mode === "terminal"} onClick={() => setMode("terminal")}>
            Terminal
          </Button>
          <Button active={mode === "commands"} onClick={() => setMode("commands")}>
            Commands
          </Button>
        </span>

        <label className="cli-label">
          Node:
          <select
            className="cli-select"
            value={selectedNode}
            onChange={(e) => setSelectedNode(e.target.value)}
          >
            <option value="">—</option>
            {nodes.map((n) => (
              <option key={n.node_id} value={n.node_id}>{n.node_id}</option>
            ))}
          </select>
        </label>

        {mode === "commands" && (
          <>
            <label className="cli-label">
              Command:
              <select
                className="cli-select"
                value={selectedCommand}
                onChange={(e) => setSelectedCommand(e.target.value)}
              >
                {commands.map((cmd) => (
                  <option key={cmd} value={cmd}>{cmd}</option>
                ))}
              </select>
            </label>
            <Button
              variant="primary"
              onClick={handleRun}
              disabled={loading || !selectedNode || !selectedCommand}
            >
              {loading ? "Running..." : "Run"}
            </Button>
          </>
        )}

        {(mode === "terminal" ? activeSession : selectedNode) && (
          <Button icon="download" onClick={handleDownloadConfig} title="Download running config">
            Config
          </Button>
        )}
        {downloadError && <span className="cli-error">{downloadError}</span>}

        <span className="cli-spring" />
        <IconButton icon="minus" label="Decrease font size" onClick={() => setFontSize((s) => Math.max(9, s - 1))} />
        <IconButton icon="plus" label="Increase font size" onClick={() => setFontSize((s) => Math.min(18, s + 1))} />
        <IconButton icon="x" label="Close CLI drawer" onClick={onClose} />
      </div>

      {mode === "terminal" && openSessions.length > 0 && (
        <div className="cli-tabs">
          <Tabs
            label="Terminal sessions"
            tabs={sessionTabs}
            active={activeSession ?? ""}
            onSelect={setActiveSession}
            onClose={closeSession}
          />
        </div>
      )}

      <div className="cli-content">
        {mode === "terminal" ? (
          openSessions.length > 0 ? (
            <PersistentTerminal
              sessions={openSessions}
              activeNodeId={activeSession}
              runtimeSessionId={runtimeSessionId}
              onSessionStatusChange={handleSessionStatus}
              fontSize={fontSize}
            />
          ) : (
            <div className="cli-placeholder">Select a node to open an interactive terminal.</div>
          )
        ) : (
          <div className="cli-output" style={{ fontSize }}>
            {loading && <pre className="cli-pre cli-pre--warn">Running command...</pre>}
            {error && <pre className="cli-pre cli-pre--fail">{error}</pre>}
            {output && <pre className="cli-pre">{output}</pre>}
            {!loading && !error && !output && (
              <pre className="cli-pre cli-pre--dim">Select a node and command, then click Run.</pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
