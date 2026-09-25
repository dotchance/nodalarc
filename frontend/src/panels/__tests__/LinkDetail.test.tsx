// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LinkDetail, formatRate } from "../LinkDetail";
import type { LinkHistoryEvent, LinkHistoryPage, LinkState, StateSnapshot } from "../../types";

function link(overrides: Partial<LinkState> = {}): LinkState {
  return {
    node_a: "leo-sat-p00s00",
    node_b: "meo-sat-p00s00",
    state: "active",
    link_type: "isl",
    link_reason: "link_state_snapshot",
    latency_ms: 42,
    transmit_mbps_a: 1000,
    receive_mbps_a: 1000,
    transmit_mbps_b: 1000,
    receive_mbps_b: 1000,
    range_km: 12000,
    traffic_load_pct: null,
    interface_a: "isl0",
    interface_b: "isl1",
    link_rule_id: "leo-to-meo-relay-candidates",
    topology_mode: "nearest_n",
    endpoint_segments: ["leo", "meo"],
    ...overrides,
  };
}

function snapshot(): StateSnapshot {
  return {
    sim_time: "2026-01-01T00:00:00Z",
    wall_time: "2026-01-01T00:00:00Z",
    schema_version: 1,
    session_id: "test",
    nodes: [],
    links: [],
    kernel_actual_pairs: [],
    traced_paths: [],
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
    history_recording: null,
  };
}

function historyPage(overrides: Partial<LinkHistoryPage> = {}): LinkHistoryPage {
  return {
    events: [],
    returned: 0,
    total: 0,
    next_cursor: null,
    retained_from: null,
    ...overrides,
  };
}

function historyEvent(
  sim_time: string,
  event_type: LinkHistoryEvent["event_type"],
): LinkHistoryEvent {
  return {
    id: 1,
    session_id: "run-1",
    sim_time,
    wall_time: sim_time,
    event_type,
    node_a: "leo-sat-p00s00",
    node_b: "meo-sat-p00s00",
    interface_a: null,
    interface_b: null,
    latency_ms: 42,
    range_km: 12000,
    reason: null,
  };
}

describe("LinkDetail", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => historyPage() }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("shows the declared rule, topology, and endpoint segments for a selected link", () => {
    render(<LinkDetail link={link()} snapshot={snapshot()} />);

    expect(screen.getByText("Rule")).toBeTruthy();
    expect(screen.getByText("leo-to-meo-relay-candidates")).toBeTruthy();
    expect(screen.getByText("Topology")).toBeTruthy();
    expect(screen.getByText("nearest_n")).toBeTruthy();
    expect(screen.getByText("Segments")).toBeTruthy();
    expect(screen.getByText("leo ↔ meo")).toBeTruthy();
  });

  it("states why no history is shown when the session is not recorded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({
          code: "history.not_recorded",
          message: "History recording is off for this session",
        }),
      }),
    );
    render(<LinkDetail link={link()} snapshot={snapshot()} />);

    expect(await screen.findByText("History recording is off for this session")).toBeTruthy();
  });

  it("asks for the newest events of this one link and shows them oldest first, X of Y", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () =>
          historyPage({
            events: [
              historyEvent("2026-01-01T00:00:09+00:00", "LatencyUpdate"),
              historyEvent("2026-01-01T00:00:05+00:00", "LinkUp"),
            ],
            returned: 2,
            total: 57,
            next_cursor: "cursor",
          }),
      }),
    );
    render(<LinkDetail link={link()} snapshot={snapshot()} />);

    expect(await screen.findByText("History (last 2 of 57)")).toBeTruthy();
    const url = new URL(String((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]![0]));
    expect(url.pathname).toBe("/api/v1/links");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      node: "leo-sat-p00s00",
      peer: "meo-sat-p00s00",
      order: "newest_first",
      limit: "20",
    });
    const times = screen.getAllByText(/^00:00:0\d$/).map((el) => el.textContent);
    expect(times).toEqual(["00:00:05", "00:00:09"]);
    expect(screen.queryByText(/dropped to keep history within its size budget/)).toBeNull();
  });

  it("says when the size budget dropped older events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () =>
          historyPage({
            events: [historyEvent("2026-01-01T00:00:05+00:00", "LinkUp")],
            returned: 1,
            total: 1,
            retained_from: "2026-01-01T00:00:01+00:00",
          }),
      }),
    );
    render(<LinkDetail link={link()} snapshot={snapshot()} />);

    expect(
      await screen.findByText("Older events were dropped to keep history within its size budget"),
    ).toBeTruthy();
  });

  it("draws each direction from its sender's TX to its receiver's RX and highlights the lower", () => {
    render(
      <LinkDetail
        link={link({
          transmit_mbps_a: 5000,
          receive_mbps_a: 5000,
          transmit_mbps_b: 1000,
          receive_mbps_b: 1000,
        })}
        snapshot={snapshot()}
      />,
    );

    const rates = within(screen.getByRole("group", { name: "Terminal rates" }));
    const values = rates.getAllByText(/Gb\/s|Mb\/s/).map((el) => ({
      text: el.textContent,
      limiting: el.getAttribute("data-limiting") === "true",
    }));
    // Row order: A's TX → B's RX, then A's RX ← B's TX.
    expect(values).toEqual([
      { text: "TX5 Gb/s", limiting: false },
      { text: "RX1 Gb/s", limiting: true },
      { text: "RX5 Gb/s", limiting: false },
      { text: "TX1 Gb/s", limiting: true },
    ]);
    expect(rates.getByText("leo-sat-p00s00")).toBeTruthy();
    expect(rates.getByText("meo-sat-p00s00")).toBeTruthy();
    expect(screen.queryByText("leo-sat-p00s00 → meo-sat-p00s00")).toBeNull();
  });

  it("highlights nothing in a direction whose two rates are equal", () => {
    render(<LinkDetail link={link()} snapshot={snapshot()} />);

    const rates = within(screen.getByRole("group", { name: "Terminal rates" }));
    expect(rates.getAllByText(/Gb\/s|Mb\/s/).some((el) => el.hasAttribute("data-limiting"))).toBe(
      false,
    );
  });
});

describe("formatRate", () => {
  it("writes Gb/s from 1000 Mb/s up and Mb/s below, keeping one declared decimal", () => {
    expect([200_000, 5000, 1200, 1000, 600, 23.6, 3].map(formatRate)).toEqual([
      "200 Gb/s",
      "5 Gb/s",
      "1.2 Gb/s",
      "1 Gb/s",
      "600 Mb/s",
      "23.6 Mb/s",
      "3 Mb/s",
    ]);
  });
});
