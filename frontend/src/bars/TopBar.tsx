// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Top bar — session entry, clocks, status chip strip, playback, mode select. */

import { useEffect, useRef, useState } from "react";
import { formatTime, formatDuration } from "../translate";
import { schedulerOpsLabel } from "../explain/reasons";
import { Icon } from "../ui/icons/Icon";
import { Button } from "../ui/Button";
import { StatusDot, type StatusTone } from "../ui/Badge";
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
    <span ref={rootRef} className="topbar-actuation">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={`${label}; show details`}
        className={`topbar-chip topbar-chip--button topbar-chip--${dirty ? "fail" : "warn"}`}
        title={title}
      >
        <StatusDot tone={dirty ? "fail" : "warn"} />
        {label}
      </button>
      {open && (
        <div role="dialog" aria-label="Actuation condition details" className="topbar-actuation-popover">
          <div className="topbar-actuation-head">
            <strong>{label}</strong>
            <Button onClick={() => setOpen(false)}>Close</Button>
          </div>
          {notices.map((notice) => (
            <div key={`${notice.gs_id}:${notice.reason_code}:${notice.since ?? "unknown"}`} className="topbar-actuation-notice">
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
  onShowHelp: () => void;
}

export function TopBar({ snapshot, historicalMode, onToggleHistorical, activeSessionName, switching, onOpenCatalog, playbackPaused, playbackSpeed, playbackLoading, onPlaybackPause, onPlaybackResume, onPlaybackSetSpeed, onSeekToNow, onShowHelp }: TopBarProps) {
  const healthStatus = snapshot?.network_health.status ?? "unknown";
  const actuationNotices = snapshot?.actuation_notices ?? [];
  const actuationDirty = actuationNotices.some((n) => n.actuation_state === "kernel_dirty");
  const healthTone: StatusTone =
    healthStatus === "converged" ? "ok" : healthStatus === "converging" ? "warn" : "fail";

  return (
    <div className="area-topbar topbar">
      <button
        onClick={onOpenCatalog}
        disabled={switching}
        className="topbar-session"
        style={switching ? { cursor: "wait" } : undefined}
        title="Open session catalog"
      >
        {activeSessionName ?? snapshot?.constellation_name ?? "NodalArc"}
      </button>
      {snapshot?.routing_stack && <span className="topbar-stack">{snapshot.routing_stack}</span>}

      <span className="topbar-clocks" title="Sim and wall clocks, digit-aligned so divergence reads at a glance">
        <span className="topbar-clock-sim">Sim&nbsp;&nbsp;{snapshot ? formatTime(snapshot.sim_time) : "--:--:--"}</span>
        <span className="topbar-clock-wall">Wall&nbsp;{snapshot ? formatTime(snapshot.wall_time) : "--:--:--"}</span>
      </span>

      <div className="topbar-chips">
        <span className={`topbar-chip topbar-chip--${healthTone}`} title={`Network: ${healthStatus}`}>
          <StatusDot tone={healthTone} />
          {healthStatus}
          {healthStatus === "converging" && snapshot?.network_health.converging_since_ms != null && (
            <span className="topbar-chip-extra">({formatDuration(snapshot.network_health.converging_since_ms)})</span>
          )}
        </span>
        {snapshot?.network_health.last_convergence_ms != null && (
          <span className="topbar-chip" title="Last convergence duration">
            conv {formatDuration(snapshot.network_health.last_convergence_ms)}
          </span>
        )}
        {actuationNotices.length > 0 && (
          <ActuationNoticeButton notices={actuationNotices} dirty={actuationDirty} />
        )}
      </div>

      <div className="topbar-playback">
        <button
          onClick={playbackPaused ? onPlaybackResume : onPlaybackPause}
          disabled={playbackLoading}
          className={`topbar-play${playbackPaused ? " topbar-play--paused" : ""}`}
          style={playbackLoading ? { cursor: "wait" } : undefined}
          title={playbackPaused ? "Resume (Space)" : "Pause (Space)"}
          aria-label={playbackPaused ? "Resume" : "Pause"}
        >
          <Icon name={playbackPaused ? "play" : "pause"} size={13} />
        </button>
        <select
          value={playbackSpeed}
          onChange={(e) => onPlaybackSetSpeed(Number(e.target.value))}
          disabled={playbackLoading}
          className="topbar-select"
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
            className="topbar-pacing"
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
          className="topbar-now"
          style={playbackLoading ? { cursor: "wait" } : undefined}
          title="Reset sim time to wall-clock now"
        >
          Now
        </button>
      </div>

      <div className="topbar-spring" />

      <div className="topbar-mode">
        <StatusDot tone={historicalMode ? "warn" : "ok"} title={historicalMode ? "Historical" : "Live"} />
        <select
          value={historicalMode ? "historical" : "live"}
          onChange={(e) => {
            const next = e.target.value === "historical";
            if (next !== historicalMode) onToggleHistorical();
          }}
          className="topbar-select topbar-select--mode"
        >
          <option value="live">Live</option>
          <option value="historical">Historical</option>
        </select>
        <button className="topbar-help" onClick={onShowHelp} title="Keyboard shortcuts and about (?)" aria-label="Help">
          <Icon name="info" size={14} />
        </button>
      </div>
    </div>
  );
}
