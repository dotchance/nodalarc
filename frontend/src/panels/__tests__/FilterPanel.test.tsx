// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FilterPanel } from "../FilterPanel";
import type { NodeState, StateSnapshot } from "../../types";

function node(node_id: string, segment_id: string, tags: string[]): NodeState {
  return {
    node_id,
    node_type: node_id.includes("gs-") ? "ground_station" : "satellite",
    lat_deg: 0,
    lon_deg: 0,
    alt_km: 550,
    vel_x_km_s: 0,
    vel_y_km_s: 0,
    vel_z_km_s: 0,
    plane: node_id.includes("gs-") ? null : 0,
    slot: node_id.includes("gs-") ? null : 0,
    routing_area: null,
    neighbor_count: 0,
    isl_count: 0,
    gnd_count: 0,
    prefix: null,
    addresses: [],
    min_elevation_deg: null,
    beam_falloff_exponent: null,
    segment_id,
    tags,
  };
}

function snapshot(): StateSnapshot {
  return {
    sim_time: "2026-01-01T00:00:00Z",
    wall_time: "2026-01-01T00:00:00Z",
    schema_version: 1,
    session_id: "test",
    nodes: [
      node("leo-sat-p00s00", "leo", ["earth", "leo"]),
      node("meo-sat-p00s00", "meo", ["earth", "meo"]),
      node("ground-gs-denver", "ground", ["earth", "ground"]),
    ],
    links: [],
    kernel_actual_pairs: [],
    traced_paths: [],
    active_flows: [],
    recent_events: [],
    network_health: {
      status: "converged",
      converging_since_ms: null,
      unreachable_flows: 0,
      last_convergence_ms: null,
    },
    routing_stack: "isis-plain",
    constellation_name: "test",
    session_status: "ready",
    session_status_detail: null,
    playback_paused: false,
    playback_speed: 1,
    stale: false,
  };
}

describe("FilterPanel", () => {
  afterEach(() => cleanup());

  it("renders segment controls and routes fly-to by segment id", () => {
    const onFlyToSegment = vi.fn();
    const onToggleSegment = vi.fn();

    render(
      <FilterPanel
        snapshot={snapshot()}
        showIslLinks={true}
        showGroundLinks={true}
        showSatPaths={false}
        colorMode="area"
        onToggleIslLinks={vi.fn()}
        onToggleGroundLinks={vi.fn()}
        onToggleSatPaths={vi.fn()}
        onSetColorMode={vi.fn()}
        visiblePlanes={null}
        onTogglePlane={vi.fn()}
        onShowAllPlanes={vi.fn()}
        onHideAllPlanes={vi.fn()}
        visibleSegments={new Set(["leo", "ground"])}
        onToggleSegment={onToggleSegment}
        onShowAllSegments={vi.fn()}
        onHideAllSegments={vi.fn()}
        onFlyToSegment={onFlyToSegment}
      />,
    );

    const leoRow = screen.getByText("leo").closest(".filter-segment-item");
    expect(leoRow).not.toBeNull();
    expect(within(leoRow as HTMLElement).getByText(/earth, leo/)).toBeTruthy();

    fireEvent.click(within(leoRow as HTMLElement).getByRole("button", { name: /fly to segment leo/i }));
    expect(onFlyToSegment).toHaveBeenCalledWith("leo");

    fireEvent.click(within(leoRow as HTMLElement).getByRole("checkbox"));
    expect(onToggleSegment).toHaveBeenCalledWith("leo");
  });
});
