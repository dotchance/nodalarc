// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { WizardRuntimeState } from "../../catalog/wizardTypes";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { useWizardApi } = await import("../useWizardApi");

function wizardState(): WizardRuntimeState {
  return {
    step: "review",
    satelliteType: null,
    groundStationSet: {
      name: "earth-leo-starlink-pop-sites",
      description: "test",
      stations: ["edmonton"],
      file: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
    },
    constellation: {
      name: "earth-leo-walker-delta-176",
      description: "test",
      satellite_count: 176,
      constellation: "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml",
      ground_stations: "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml",
      mode: "parametric",
    },
    orbitPropagator: "j2_mean_elements",
    protocol: "isis",
    extensions: ["te", "mpls"],
    areaStrategy: "per_plane",
    routingTimers: {
      bfd: false,
      bfd_detect_multiplier: 3,
      bfd_rx_interval: 300,
      bfd_tx_interval: 300,
      isis_hello_interval: 1,
      isis_hello_multiplier: 3,
      spf_init_delay: 50,
      spf_short_delay: 200,
      spf_long_delay: 1000,
      spf_holddown: 2000,
      spf_time_to_learn: 500,
      ospf_hello_interval: 1,
      ospf_dead_interval: 3,
      ospf_spf_delay: 50,
      ospf_spf_initial_hold: 200,
      ospf_spf_max_hold: 1000,
    },
  };
}

describe("useWizardApi", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ yaml: "session: {}\n" }),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  it("sends the selected orbit propagator when generating a session", async () => {
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });

    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    expect(fetchMock.mock.calls[0]![0]).toBe("http://test:8080/api/v1/session/generate");
    expect(body.orbit_propagator).toBe("j2_mean_elements");
    expect(body.constellation).toBe("earth-leo-walker-delta-176");
    expect(body.protocol).toBe("isis");
    expect(body.extensions).toEqual(["te", "mpls"]);
    expect(body.area_strategy).toBe("per_plane");
  });

  it("sends grammar-shaped timers and never the retired routing_config", async () => {
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });

    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    expect(body.routing_config).toBeUndefined();
    expect(body.timers).toEqual({
      hello_interval_s: 1,
      hold_interval_s: 3,
      spf: {
        init_delay_ms: 50,
        short_delay_ms: 200,
        long_delay_ms: 1000,
        holddown_ms: 2000,
        time_to_learn_ms: 500,
      },
      bfd: {
        enabled: false,
        detect_multiplier: 3,
        rx_interval_ms: 300,
        tx_interval_ms: 300,
      },
    });
  });

  it("maps ospf panel timers onto the neutral grammar shape", async () => {
    const { result } = renderHook(() => useWizardApi());
    const state = wizardState();
    state.protocol = "ospf";
    state.routingTimers.ospf_hello_interval = 2;
    state.routingTimers.ospf_dead_interval = 8;
    state.routingTimers.bfd = true;

    await act(async () => { await result.current.generate(state); });

    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    const timers = body.timers as Record<string, unknown>;
    expect(timers.hello_interval_s).toBe(2);
    expect(timers.hold_interval_s).toBe(8);
    expect((timers.bfd as Record<string, unknown>).enabled).toBe(true);
  });

  it("surfaces backend orbit contract failures instead of masking them", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({ error: "orbit_propagator is required" }),
    });
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });

    expect(result.current.error).toBe("orbit_propagator is required");
  });
});
