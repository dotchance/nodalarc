// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Top bar — session info, sim time, health indicator, mode selector. */

import { useEffect, useRef, useState } from "react";
import { formatTime, formatDuration } from "../translate";
import { schedulerOpsLabel } from "../explain/reasons";
import type { ActuationNotice, StateSnapshot } from "../types";

function pairLabel(pair: string[] | undefined): string {
  if (!pair || pair.length === 0) return "none";
  return pair.join(" -> ");
}

function pairList(pairs: string[][] | undefined): string {
  if (!pairs || pairs.length === 0) return "none";
  return pairs.map(pairLabel).join(", ");
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function firstText(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function proofFailure(notice: ActuationNotice): string {
  const details = objectValue(notice.last_event?.details);
  const results = Array.isArray(details?.node_agent_results) ? details.node_agent_results : [];
  for (const raw of results) {
    const result = objectValue(raw);
    const proof = firstText(result?.proof_summary) ?? firstText(result?.error_message);
    if (proof) return proof;
  }
  return notice.message || "No kernel proof detail was reported.";
}

function recoveryText(notice: ActuationNotice): string {
  const recovery = notice.recovery_status ?? {};
  if (recovery.operator_action_required === true || recovery.verify_exhausted === true) {
    return `Run operator repair for ${notice.gs_id}, or restart/redeploy after capturing evidence.`;
  }
  const nextVerify = firstText(recovery.next_verify_after);
  if (nextVerify) return `Waiting for read-only kernel verification at ${nextVerify}.`;
  return `Read-only kernel verification is still active for ${notice.gs_id}.`;
}

function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function noticeSummary(notices: ActuationNotice[]): string {
  const faultCount = notices.filter((n) => n.actuation_state === "kernel_dirty").length;
  const warningCount = notices.length - faultCount;
  if (faultCount > 0 && warningCount > 0) {
    return `${plural(faultCount, "actuation fault")}, ${plural(warningCount, "warning")}`;
  }
  if (faultCount > 0) return plural(faultCount, "actuation fault");
  return plural(warningCount, "actuation warning");
}

function ActuationNoticeButton({ notices, dirty }: { notices: ActuationNotice[]; dirty: boolean }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  const label = noticeSummary(notices);
  const title = notices.map((n) => `${n.gs_id}: ${schedulerOpsLabel(n.reason_code)}`).join("\n");

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      if (root && event.target instanceof Node && !root.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  return (
    <span ref={rootRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={`${label}; show details`}
        style={{
          color: dirty ? "var(--ws-disconnected)" : "var(--ws-reconnecting)",
          fontWeight: 600,
          background: "transparent",
          border: "1px solid currentColor",
          borderRadius: 4,
          padding: "2px 8px",
          fontSize: 11,
          cursor: "pointer",
        }}
        title={title}
      >
        {label}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Actuation condition details"
          style={{
            position: "absolute",
            zIndex: 1000,
            top: "calc(100% + 8px)",
            left: 0,
            width: 440,
            maxWidth: "calc(100vw - 32px)",
            padding: 12,
            border: "1px solid var(--border)",
            borderRadius: 6,
            background: "var(--bg-panel)",
            boxShadow: "0 10px 30px rgba(0, 0, 0, 0.45)",
            color: "var(--text-primary)",
            display: "grid",
            gap: 10,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <strong>{label}</strong>
            <button
              type="button"
              onClick={() => setOpen(false)}
              style={{
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "transparent",
                color: "var(--text-secondary)",
                cursor: "pointer",
                fontSize: 11,
              }}
            >
              Close
            </button>
          </div>
          {notices.map((notice) => (
            <div
              key={`${notice.gs_id}:${notice.reason_code}:${notice.since ?? "unknown"}`}
              style={{
                display: "grid",
                gap: 4,
                paddingTop: 8,
                borderTop: "1px solid var(--border)",
                lineHeight: 1.35,
              }}
            >
              <div><strong>Ground station:</strong> {notice.gs_id}</div>
              <div><strong>State:</strong> {notice.actuation_state}</div>
              <div><strong>Reason:</strong> {schedulerOpsLabel(notice.reason_code)}</div>
              <div>
                <strong>Impact:</strong>{" "}
                {notice.blocking_new_ground_link_up
                  ? "new ground link changes are suppressed for this GS"
                  : "automatic recovery is active; new ground links are not blocked"}
              </div>
              <div><strong>OME wants:</strong> {pairList(notice.desired_pairs_for_gs)}</div>
              <div><strong>Kernel/actual differs:</strong> {pairList(notice.affected_pairs.length ? notice.affected_pairs : notice.actual_pairs_for_gs)}</div>
              <div><strong>Last proof failure:</strong> {proofFailure(notice)}</div>
              <div><strong>Recovery:</strong> {recoveryText(notice)}</div>
            </div>
          ))}
        </div>
      )}
    </span>
  );
}

interface TopBarProps {
  snapshot: StateSnapshot | null;
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
  onSeekToNow: () => void;
}

export function TopBar({ snapshot, historicalMode, onToggleHistorical, activeSessionName, switching, onOpenCatalog, playbackPaused, playbackSpeed, playbackLoading, onPlaybackPause, onPlaybackResume, onPlaybackSetSpeed, onSeekToNow }: TopBarProps) {
  const healthStatus = snapshot?.network_health.status ?? "unknown";
  const actuationNotices = snapshot?.actuation_notices ?? [];
  const actuationDirty = actuationNotices.some((n) => n.actuation_state === "kernel_dirty");
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
      <span
        style={{
          display: "inline-flex",
          flexDirection: "column",
          lineHeight: 1.25,
          fontFamily: "var(--font-mono, monospace)",
          fontSize: 11,
        }}
        title="Sim and wall clocks, digit-aligned so divergence reads at a glance"
      >
        <span style={{ color: "var(--text-secondary)" }}>
          Sim&nbsp;&nbsp;{snapshot ? formatTime(snapshot.sim_time) : "--:--:--"}
        </span>
        <span style={{ color: "var(--text-dim)" }}>
          Wall&nbsp;{snapshot ? formatTime(snapshot.wall_time) : "--:--:--"}
        </span>
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
      {actuationNotices.length > 0 && (
        <ActuationNoticeButton notices={actuationNotices} dirty={actuationDirty} />
      )}
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
          <option value={1}>1x</option>
          <option value={5}>5x</option>
          <option value={10}>10x</option>
          <option value={30}>30x</option>
          <option value={60}>60x</option>
          <option value={120}>120x</option>
          <option value={300}>300x</option>
        </select>
        {snapshot?.pacing_degraded && snapshot.playback_achieved != null && (
          <span
            style={{
              fontSize: 11,
              color: "var(--ws-reconnecting)",
              whiteSpace: "nowrap",
            }}
            title={
              `Engine is delivering ${snapshot.playback_achieved.toFixed(1)}x of the ` +
              `commanded ${playbackSpeed}x. The clock is honest: simulation time advances ` +
              `at the delivered rate.`
            }
          >
            delivering {snapshot.playback_achieved.toFixed(1)}x
          </span>
        )}
        <button
          onClick={onSeekToNow}
          disabled={playbackLoading}
          style={{
            padding: "2px 6px",
            borderRadius: 4,
            border: "1px solid var(--border)",
            background: "transparent",
            color: "var(--text-secondary)",
            fontSize: 11,
            cursor: playbackLoading ? "wait" : "pointer",
          }}
          title="Reset sim time to wall-clock now"
        >
          Now
        </button>
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
