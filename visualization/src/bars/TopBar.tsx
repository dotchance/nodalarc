/** Top bar — session info, sim time, health indicator, mode selector. */

import { useRef } from "react";
import { formatTime, formatDuration } from "../translate";
import type { StateSnapshot } from "../types";

interface TopBarProps {
  snapshot: StateSnapshot | null;
  connected: boolean;
  historicalMode: boolean;
  onToggleHistorical: () => void;
}

/** Compute compression factor from recent snapshots. */
function useCompressionFactor(snapshot: StateSnapshot | null): string {
  const historyRef = useRef<{ simMs: number; wallMs: number }[]>([]);

  if (snapshot) {
    const simMs = new Date(snapshot.sim_time).getTime();
    const wallMs = Date.now();
    const history = historyRef.current;
    history.push({ simMs, wallMs });
    if (history.length > 5) history.shift();

    if (history.length >= 2) {
      const first = history[0]!;
      const last = history[history.length - 1]!;
      const simDelta = last.simMs - first.simMs;
      const wallDelta = last.wallMs - first.wallMs;
      if (wallDelta > 0) {
        const factor = simDelta / wallDelta;
        return `${factor.toFixed(1)}x`;
      }
    }
  }
  return "--";
}

export function TopBar({ snapshot, connected: _connected, historicalMode, onToggleHistorical }: TopBarProps) {
  const compressionFactor = useCompressionFactor(snapshot);
  const healthStatus = snapshot?.network_health.status ?? "unknown";
  const healthColor =
    healthStatus === "converged"
      ? "var(--ws-connected)"
      : healthStatus === "converging"
        ? "var(--ws-reconnecting)"
        : "var(--ws-disconnected)";

  return (
    <div
      className="area-topbar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "0 16px",
        background: "var(--bg-bar)",
        borderBottom: "1px solid var(--border)",
        fontSize: 12,
      }}
    >
      <span
        style={{ fontWeight: 600, color: "var(--accent-blue)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        title={snapshot ? `${snapshot.constellation_name ?? ""}  |  ${snapshot.routing_stack ?? ""}` : "Nodal Arc"}
      >
        {snapshot?.constellation_name ?? "Nodal Arc"}
      </span>
      {snapshot?.routing_stack && (
        <span style={{ color: "var(--text-dim)", fontSize: 10 }}>
          {snapshot.routing_stack}
        </span>
      )}
      <span style={{ color: "var(--text-secondary)" }}>
        Sim: {snapshot ? formatTime(snapshot.sim_time) : "--:--:--"}
      </span>
      <span style={{ color: "var(--text-dim)" }}>
        Wall: {snapshot ? formatTime(snapshot.wall_time) : "--:--:--"}
      </span>
      <span style={{ color: "var(--text-dim)" }}>
        {compressionFactor}
      </span>
      {snapshot?.network_health.last_convergence_ms != null && (
        <span style={{ color: "var(--text-dim)" }}>
          Conv: {formatDuration(snapshot.network_health.last_convergence_ms)}
        </span>
      )}
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: healthColor,
          display: "inline-block",
        }}
        title={`Network: ${healthStatus}`}
      />
      <span style={{ color: "var(--text-secondary)" }}>
        {healthStatus}
        {healthStatus === "converging" && snapshot?.network_health.converging_since_ms != null && (
          <span style={{ color: "var(--text-dim)", marginLeft: 4 }}>
            ({formatDuration(snapshot.network_health.converging_since_ms)})
          </span>
        )}
      </span>
      <div style={{ flex: 1 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: historicalMode ? "var(--ws-reconnecting)" : "var(--ws-connected)",
            display: "inline-block",
          }}
        />
        <select
          value={historicalMode ? "historical" : "live"}
          onChange={() => onToggleHistorical()}
          style={{
            padding: "2px 6px",
            borderRadius: 4,
            border: "1px solid var(--border)",
            background: "transparent",
            color: "var(--text-secondary)",
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          <option value="live">Live</option>
          <option value="historical">Historical</option>
        </select>
      </div>
    </div>
  );
}
