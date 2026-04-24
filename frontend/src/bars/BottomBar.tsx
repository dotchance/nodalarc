// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Bottom bar — link/node counts, convergence, WS status. */

import { WS_URL } from "../config";
import type { StateSnapshot } from "../types";

interface BottomBarProps {
  snapshot: StateSnapshot | null;
  connected: boolean;
  historicalMode?: boolean;
}

export function BottomBar({ snapshot, connected, historicalMode }: BottomBarProps) {
  const activeLinks = snapshot?.links.filter((l) => l.state === "active").length ?? 0;
  const totalLinks = snapshot?.links.length ?? 0;
  const nodeCount = snapshot?.nodes.length ?? 0;
  const flowCount = snapshot?.active_flows.length ?? 0;
  const convergence = snapshot?.network_health.status ?? "unknown";
  const unreachableFlows = snapshot?.network_health.unreachable_flows ?? 0;

  const convColor =
    convergence === "converged"
      ? "var(--ws-connected)"
      : convergence === "converging" || convergence === "stabilizing"
        ? "var(--ws-reconnecting)"
        : convergence === "degraded"
          ? "var(--ws-disconnected)"
          : "var(--text-secondary)";

  const wsColor = historicalMode
    ? "var(--ws-reconnecting)"
    : connected
      ? "var(--ws-connected)"
      : "var(--ws-disconnected)";
  const wsLabel = historicalMode
    ? "Historical"
    : connected
      ? "Connected"
      : "Disconnected";

  return (
    <div
      className="area-bottombar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 16px",
        background: "var(--bg-bar)",
        borderTop: "1px solid var(--border)",
        fontSize: 11,
        color: "var(--text-secondary)",
      }}
    >
      <span>Links {activeLinks}/{totalLinks}</span>
      <span>Nodes {nodeCount}</span>
      <span style={{ color: convColor }}>
        Conv: {convergence}
        {convergence === "degraded" && unreachableFlows > 0 ? ` (${unreachableFlows} flows)` : ""}
      </span>
      <span>Flows {flowCount}</span>
      <div style={{ flex: 1 }} />
      <span
        title={WS_URL}
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: wsColor,
          display: "inline-block",
          cursor: "help",
        }}
      />
      <span style={{ color: wsColor }} title={WS_URL}>{wsLabel}</span>
      <span style={{ color: "var(--accent-blue)", fontSize: 11, fontFamily: "var(--font-family)" }}>
        build: {typeof __BUILD_HASH__ !== "undefined" ? __BUILD_HASH__ : "dev"}
      </span>
    </div>
  );
}
