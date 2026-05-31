// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect } from "vitest";
import { groundStationFamily } from "../groundStationFamily";
import type { ActuationNotice, LinkState } from "../../types";

function link(a: string, b: string, state = "active"): LinkState {
  return {
    node_a: a,
    node_b: b,
    state,
    link_type: null,
    link_reason: null,
    latency_ms: 0,
    bandwidth_mbps: 0,
    range_km: 0,
    traffic_load_pct: null,
    interface_a: "",
    interface_b: "",
  } as LinkState;
}

function notice(gs_id: string, blocking: boolean, reason = "kernel dirty"): ActuationNotice {
  return {
    gs_id,
    actuation_state: blocking ? "kernel_dirty" : "in_flight",
    reason_code: "KERNEL_DIRTY",
    message: reason,
    since: null,
    blocking_new_ground_link_up: blocking,
    affected_pairs: [],
    desired_pairs_for_gs: [],
    actual_pairs_for_gs: [],
    ome_visible_scheduled_pairs_for_gs: [],
    recovery_status: {},
    last_event: {},
  };
}

describe("groundStationFamily (default-state snapshot approximation)", () => {
  it("is faulted (with reason) when a blocking actuation notice exists", () => {
    const r = groundStationFamily("gs-1", [link("gs-1", "sat-1")], [notice("gs-1", true, "BatchLinkUp failed")]);
    expect(r.family).toBe("faulted");
    expect(r.reason).toBe("BatchLinkUp failed");
  });

  it("is in_flight (degraded, not faulted) for a non-blocking actuation notice", () => {
    const r = groundStationFamily("gs-1", [], [notice("gs-1", false)]);
    expect(r.family).toBe("in_flight");
  });

  it("a fault notice wins over an active link (fault dominates)", () => {
    const r = groundStationFamily("gs-1", [link("gs-1", "sat-1")], [notice("gs-1", true)]);
    expect(r.family).toBe("faulted");
  });

  it("is connected when a GS has an active ground link and no notice", () => {
    expect(groundStationFamily("gs-1", [link("sat-2", "gs-1")], []).family).toBe("connected");
  });

  it("ignores inactive links when deciding connected", () => {
    expect(groundStationFamily("gs-1", [link("gs-1", "sat-1", "inactive")], []).family).toBe(
      "expected_no_link",
    );
  });

  it("is expected_no_link when there is no link and no notice — never eligible_unselected from the snapshot", () => {
    const r = groundStationFamily("gs-1", [link("sat-1", "sat-2")], []);
    expect(r.family).toBe("expected_no_link");
    expect(r.reason).toBeNull();
  });
});
