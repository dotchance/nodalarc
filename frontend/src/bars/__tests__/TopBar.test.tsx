// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TopBar } from "../TopBar";
import type { StateSnapshot } from "../../generated/vsApi";

afterEach(cleanup);

function snapshotWithActuationNotice(): StateSnapshot {
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
    routing_stack: "ospf",
    constellation_name: "demo",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_pause_reason: null,
    playback_coverage: null,
    playback_speed: 1,
    stale: false,
    history_recording: null,
    kernel_actual_pairs: [],
    draining_directions: [],
    contact_losses: [],
    playback_achieved: null,
    pacing_degraded: false,
    actuation_health: null,
    ops_events: [],
    ops_log_token: "t",
    debug_events: [],
    actuation_notices: [
      {
        gs_id: "gs-buenos-aires",
        actuation_state: "kernel_dirty",
        reason_code: "KERNEL_VERIFY_EXHAUSTED",
        message: "KernelInventory auto-verify exhausted for gs-buenos-aires; operator action required",
        since: "2026-06-01T12:00:01.000Z",
        blocking_new_ground_link_up: true,
        affected_pairs: [["gs-buenos-aires", "sat-P06S05"]],
        desired_pairs_for_gs: [["gs-buenos-aires", "sat-P00S08"]],
        actual_pairs_for_gs: [["gs-buenos-aires", "sat-P06S05"]],
        ome_visible_scheduled_pairs_for_gs: [["gs-buenos-aires", "sat-P00S08"]],
        recovery_status: {
          verify_attempt_count: 5,
          verify_exhausted: true,
          operator_action_required: true,
        },
        last_event: {
          details: {
            node_agent_results: [
              {
                proof_summary: "interface/qdisc/mirred checks did not match desired state",
              },
            ],
          },
        },
      },
    ],
  };
}

function renderTopBar(snapshot: StateSnapshot) {
  return render(
    <TopBar
      snapshot={snapshot}
      historicalMode={false}
      onToggleHistorical={vi.fn()}
      activeSessionName="demo"
      switching={false}
      onOpenCatalog={vi.fn()}
      playbackPaused={false}
      playbackSpeed={1}
      playbackLoading={false}
      onPlaybackPause={vi.fn()}
      onPlaybackResume={vi.fn()}
      onPlaybackSetSpeed={vi.fn()}
      onSeekToNow={vi.fn()}
      onShowHelp={vi.fn()}
    />,
  );
}

describe("TopBar actuation notices", () => {
  it("renders a human-readable clickable fault with operator details", () => {
    renderTopBar(snapshotWithActuationNotice());

    expect(screen.queryByText("Actuation 1")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /1 actuation fault/i }));

    expect(screen.getByRole("dialog", { name: /actuation condition details/i })).toBeTruthy();
    expect(screen.getByText("Ground station:")).toBeTruthy();
    expect(screen.getByText("gs-buenos-aires")).toBeTruthy();
    expect(screen.getByText("State:")).toBeTruthy();
    expect(screen.getByText("kernel_dirty")).toBeTruthy();
    expect(screen.getByText("Kernel verification exhausted")).toBeTruthy();
    expect(screen.getByText(/new ground link changes are suppressed for this GS/i)).toBeTruthy();
    expect(screen.getByText("gs-buenos-aires -> sat-P00S08")).toBeTruthy();
    expect(screen.getByText("gs-buenos-aires -> sat-P06S05")).toBeTruthy();
    expect(screen.getByText(/interface\/qdisc\/mirred checks did not match desired state/i)).toBeTruthy();
    expect(screen.getByText(/run operator repair for gs-buenos-aires/i)).toBeTruthy();
  });
  it("labels mixed actuation conditions without implying every notice is a fault", () => {
    const base = snapshotWithActuationNotice();
    const snapshot: StateSnapshot = { ...base, actuation_notices: [
      ...base.actuation_notices,
      {
        ...base.actuation_notices[0]!,
        gs_id: "gs-denver",
        actuation_state: "actuation_blocked",
        reason_code: "ACTUATION_BLOCKED",
        message: "Clean actuator failure awaiting operator decision",
        blocking_new_ground_link_up: false,
        affected_pairs: [["gs-denver", "sat-P01S01"]],
        desired_pairs_for_gs: [["gs-denver", "sat-P01S02"]],
        actual_pairs_for_gs: [["gs-denver", "sat-P01S01"]],
        ome_visible_scheduled_pairs_for_gs: [["gs-denver", "sat-P01S02"]],
      },
    ] };

    renderTopBar(snapshot);

    expect(screen.getByRole("button", { name: /1 actuation fault, 1 warning/i })).toBeTruthy();
  });

  it("closes the actuation details on Escape and outside pointer down", () => {
    renderTopBar(snapshotWithActuationNotice());

    fireEvent.click(screen.getByRole("button", { name: /1 actuation fault/i }));
    expect(screen.getByRole("dialog", { name: /actuation condition details/i })).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: /actuation condition details/i })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /1 actuation fault/i }));
    expect(screen.getByRole("dialog", { name: /actuation condition details/i })).toBeTruthy();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog", { name: /actuation condition details/i })).toBeNull();
  });

});

describe("TopBar clock stopped at the end of the ephemeris data", () => {
  it("says the data ended, when, and how to continue", () => {
    renderTopBar({
      ...snapshotWithActuationNotice(),
      actuation_notices: [],
      playback_paused: true,
      playback_pause_reason: "ephemeris_coverage_ended",
      playback_coverage: {
        body_id: "earth",
        usable_from: "2000-01-01T01:10:00Z",
        usable_until: "2126-11-02T17:50:00Z",
      },
    });

    const status = screen.getByRole("status");
    expect(status.textContent).toBe("Ephemeris data ends 2126-11-02 17:50:00 UTC");
    expect(status.getAttribute("title")).toBe(
      "The earth ephemeris data supports this session from 2000-01-01 01:10:00 UTC to " +
        "2126-11-02 17:50:00 UTC. The clock stopped at the last tick the data supports; " +
        "seek back into that span to continue.",
    );
  });

  it("says nothing about the data on a pause the user made", () => {
    renderTopBar({ ...snapshotWithActuationNotice(), actuation_notices: [], playback_paused: true });

    expect(screen.queryByText(/Ephemeris data ends/)).toBeNull();
  });
});
