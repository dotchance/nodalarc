// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BottomBar } from "../BottomBar";
import type { HistoryRecordingState, StateSnapshot } from "../../generated/vsApi";

// Vite defines the build hash at build time; vitest does not.
beforeEach(() => vi.stubGlobal("__BUILD_HASH__", "test"));
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

function snapshot(history_recording: HistoryRecordingState | null): StateSnapshot {
  return {
    msg_type: "state_snapshot",
    debug_control: [],
    debug_subscription_open: false,
    sim_time: "2026-06-01T12:00:00.000Z",
    wall_time: "2026-06-01T12:00:00.000Z",
    session_id: "test",
    nodes: [],
    links: [],
    traced_paths: [],
    recent_events: [],
    routing_stack: "isis",
    constellation_name: "demo",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_pause_reason: null,
    playback_coverage: null,
    playback_speed: 1,
    stale: false,
    history_recording,
    kernel_actual_pairs: [],
    draining_directions: [],
    contact_losses: [],
    playback_achieved: null,
    pacing_degraded: false,
    actuation_notices: [],
    actuation_health: null,
    ops_events: [],
    ops_log_token: "t",
    debug_events: [],
  };
}

describe("BottomBar history recording", () => {
  it("says nothing about history for a run deployed without recording", () => {
    render(<BottomBar snapshot={snapshot(null)} connected />);

    expect(screen.queryByText(/history/i)).toBeNull();
  });

  it("shows a recording run", () => {
    render(<BottomBar snapshot={snapshot({ state: "recording", error: null })} connected />);

    expect(screen.getByText("Recording history").className).toContain("bottombar-ok");
  });

  it("shows a stopped recording with its reason", () => {
    render(
      <BottomBar
        snapshot={snapshot({ state: "stopped", error: "failed to record LinkUp" })}
        connected
      />,
    );

    const stopped = screen.getByText("History recording stopped");
    expect(stopped.className).toContain("bottombar-fail");
    expect(stopped.getAttribute("title")).toBe("failed to record LinkUp");
  });
});
