// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StateSnapshot } from "../../types";
import { TopBar } from "../TopBar";

afterEach(cleanup);

function snapshotWithActuationNotice(): StateSnapshot {
  return {
    sim_time: "2026-06-01T12:00:00.000Z",
    wall_time: "2026-06-01T12:00:00.000Z",
    schema_version: 1,
    session_id: "test",
    nodes: [],
    links: [],
    traced_paths: [],
    active_flows: [],
    recent_events: [],
    network_health: {
      status: "converged",
      converging_since_ms: null,
      unreachable_flows: 0,
      last_convergence_ms: 42,
    },
    routing_stack: "ospf",
    constellation_name: "demo",
    session_status: "running",
    session_status_detail: null,
    playback_paused: false,
    playback_speed: 1,
    stale: false,
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
      connected
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
    expect(screen.getByText("Kernel Verify Exhausted")).toBeTruthy();
    expect(screen.getByText(/new ground link changes are suppressed for this GS/i)).toBeTruthy();
    expect(screen.getByText("gs-buenos-aires -> sat-P00S08")).toBeTruthy();
    expect(screen.getByText("gs-buenos-aires -> sat-P06S05")).toBeTruthy();
    expect(screen.getByText(/interface\/qdisc\/mirred checks did not match desired state/i)).toBeTruthy();
    expect(screen.getByText(/run operator repair for gs-buenos-aires/i)).toBeTruthy();
  });
});
