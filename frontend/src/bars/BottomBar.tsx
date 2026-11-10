// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Bottom bar — link/node counts, convergence, history recording, WS status, build provenance.
 *  The build hash line is part of the deploy drift-gate workflow — keep it. */

import { WS_URL } from "../config";
import { StatusDot } from "../ui/Badge";
import type { StateSnapshot } from "../generated/vsApi";

interface BottomBarProps {
  snapshot: StateSnapshot | null;
  connected: boolean;
  historicalMode?: boolean;
  logPanelOpen?: boolean;
  onToggleLogPanel?: () => void;
}

export function BottomBar({ snapshot, connected, historicalMode, logPanelOpen, onToggleLogPanel }: BottomBarProps) {
  const recording = snapshot?.history_recording ?? null;

  const wsTone = historicalMode ? "warn" : connected ? "ok" : "fail";
  const wsClass = historicalMode ? "bottombar-warn" : connected ? "bottombar-ok" : "bottombar-fail";
  const wsLabel = historicalMode ? "Historical" : connected ? "Connected" : "Disconnected";

  return (
    <div className="area-bottombar bottombar">
      {snapshot !== null && (
        <>
          <span className="bottombar-stat">
            Links {snapshot.links.filter((l) => l.state === "active").length}/{snapshot.links.length}
          </span>
          <span className="bottombar-stat">Nodes {snapshot.nodes.length}</span>
        </>
      )}
      {recording !== null && (
        <span
          className={`bottombar-stat ${recording.state === "recording" ? "bottombar-ok" : "bottombar-fail"}`}
          title={recording.error ?? "This session run is recording its history"}
        >
          {recording.state === "recording" ? "Recording history" : "History recording stopped"}
        </span>
      )}
      <div className="bottombar-spring" />
      <span title={WS_URL} className="bottombar-ws">
        <StatusDot tone={wsTone} />
      </span>
      <span className={wsClass} title={WS_URL}>{wsLabel}</span>
      {onToggleLogPanel && (
        <button
          onClick={onToggleLogPanel}
          title="System Logs"
          className={`bottombar-logs${logPanelOpen ? " bottombar-logs--open" : ""}`}
        >
          Logs
        </button>
      )}
      <span className="bottombar-build">build: {__BUILD_HASH__}</span>
    </div>
  );
}
