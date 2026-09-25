// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HistoryRecordingState, StateSnapshot } from "../../types";
import { BottomBar } from "../BottomBar";

// Vite defines the build hash at build time; vitest does not.
beforeEach(() => vi.stubGlobal("__BUILD_HASH__", "test"));
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

function snapshot(history_recording: HistoryRecordingState | null): StateSnapshot {
  return {
    sim_time: "2026-06-01T12:00:00.000Z",
    wall_time: "2026-06-01T12:00:00.000Z",
    schema_version: 1,
    session_id: "test",
    nodes: [],
    links: [],
    traced_paths: [],
    recent_events: [],
    network_health: {
      status: "converged",
      converging_since_ms: null,
      unreachable_flows: 0,
      last_convergence_ms: null,
    },
    routing_stack: "isis",
    constellation_name: "demo",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_speed: 1,
    stale: false,
    history_recording,
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
