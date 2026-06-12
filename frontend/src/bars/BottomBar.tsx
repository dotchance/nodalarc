// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Bottom bar — link/node counts, convergence, WS status, build provenance.
 *  The build hash line is part of the deploy drift-gate workflow — keep it. */

import { WS_URL } from "../config";
import { StatusDot } from "../ui/Badge";
import type { StateSnapshot } from "../types";

interface BottomBarProps {
  snapshot: StateSnapshot | null;
  connected: boolean;
  historicalMode?: boolean;
  logPanelOpen?: boolean;
  onToggleLogPanel?: () => void;
}

export function BottomBar({ snapshot, connected, historicalMode, logPanelOpen, onToggleLogPanel }: BottomBarProps) {
  const activeLinks = snapshot?.links.filter((l) => l.state === "active").length ?? 0;
  const totalLinks = snapshot?.links.length ?? 0;
  const nodeCount = snapshot?.nodes.length ?? 0;
  const flowCount = snapshot?.active_flows.length ?? 0;
  const convergence = snapshot?.network_health.status ?? "unknown";
  const unreachableFlows = snapshot?.network_health.unreachable_flows ?? 0;

  const convClass =
    convergence === "converged"
      ? "bottombar-ok"
      : convergence === "converging" || convergence === "stabilizing"
        ? "bottombar-warn"
        : convergence === "degraded"
          ? "bottombar-fail"
          : "";

  const wsTone = historicalMode ? "warn" : connected ? "ok" : "fail";
  const wsClass = historicalMode ? "bottombar-warn" : connected ? "bottombar-ok" : "bottombar-fail";
  const wsLabel = historicalMode ? "Historical" : connected ? "Connected" : "Disconnected";

  return (
    <div className="area-bottombar bottombar">
      <span className="bottombar-stat">Links {activeLinks}/{totalLinks}</span>
      <span className="bottombar-stat">Nodes {nodeCount}</span>
      <span className={`bottombar-stat ${convClass}`}>
        Conv: {convergence}
        {convergence === "degraded" && unreachableFlows > 0 ? ` (${unreachableFlows} flows)` : ""}
      </span>
      <span className="bottombar-stat">Flows {flowCount}</span>
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
