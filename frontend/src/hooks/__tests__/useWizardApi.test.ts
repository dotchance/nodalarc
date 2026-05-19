// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
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
    satelliteType: {
      name: "starlink-v2",
      description: "test",
      isl_terminals: [],
      ground_terminals: [],
    },
    groundStationSet: {
      name: "starlink-176",
      description: "test",
      stations: ["edmonton"],
      file: "configs/ground-stations/sets/starlink-176.yaml",
    },
    constellation: {
      name: "starlink-176",
      description: "test",
      satellite_count: 176,
      constellation: "configs/constellations/starlink-176.yaml",
      ground_stations: "configs/ground-stations/sets/starlink-176.yaml",
      mode: "parametric",
    },
    orbitPropagator: "j2-mean-elements",
    protocol: "isis",
    extensions: ["te", "mpls"],
    areaStrategy: "per-plane",
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
    expect(body.orbit_propagator).toBe("j2-mean-elements");
    expect(body.constellation).toBe("starlink-176");
    expect(body.protocol).toBe("isis");
    expect(body.extensions).toEqual(["te", "mpls"]);
    expect(body.area_strategy).toBe("per-plane");
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
