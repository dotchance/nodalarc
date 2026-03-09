/** Top bar — session info, sim time, health indicator, mode selector. */

import { formatTime, formatDuration } from "../translate";
import type { StateSnapshot } from "../types";

interface TopBarProps {
  snapshot: StateSnapshot | null;
  connected: boolean;
  historicalMode: boolean;
  onToggleHistorical: () => void;
  activeSessionName: string | null;
  switching: boolean;
  onOpenCatalog: () => void;
  playbackPaused: boolean;
  playbackSpeed: number;
  playbackLoading: boolean;
  onPlaybackPause: () => void;
  onPlaybackResume: () => void;
  onPlaybackSetSpeed: (factor: number) => void;
}

export function TopBar({ snapshot, connected: _connected, historicalMode, onToggleHistorical, activeSessionName, switching, onOpenCatalog, playbackPaused, playbackSpeed, playbackLoading, onPlaybackPause, onPlaybackResume, onPlaybackSetSpeed }: TopBarProps) {
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
      <button
        onClick={onOpenCatalog}
        disabled={switching}
        style={{
          fontWeight: 600,
          color: "var(--accent-blue)",
          background: "transparent",
          border: "1px solid var(--border)",
          borderRadius: 4,
          padding: "2px 8px",
          fontSize: 12,
          maxWidth: 200,
          cursor: switching ? "wait" : "pointer",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title="Open session catalog"
      >
        {activeSessionName ?? snapshot?.constellation_name ?? "Nodal Arc"}
      </button>
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
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: 8 }}>
        <button
          onClick={playbackPaused ? onPlaybackResume : onPlaybackPause}
          disabled={playbackLoading}
          style={{
            padding: "2px 8px",
            borderRadius: 4,
            border: "1px solid var(--border)",
            background: playbackPaused ? "var(--ws-reconnecting)" : "transparent",
            color: "var(--text-secondary)",
            fontSize: 11,
            cursor: playbackLoading ? "wait" : "pointer",
          }}
          title={playbackPaused ? "Resume" : "Pause"}
        >
          {playbackPaused ? "Play" : "Pause"}
        </button>
        <select
          value={playbackSpeed}
          onChange={(e) => onPlaybackSetSpeed(Number(e.target.value))}
          disabled={playbackLoading}
          style={{
            padding: "2px 4px",
            borderRadius: 4,
            border: "1px solid var(--border)",
            background: "transparent",
            color: "var(--text-secondary)",
            fontSize: 11,
          }}
          title="Playback speed"
        >
          <option value={0.25}>0.25x</option>
          <option value={0.5}>0.5x</option>
          <option value={1}>1x</option>
          <option value={2}>2x</option>
          <option value={5}>5x</option>
          <option value={10}>10x</option>
        </select>
      </div>
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
