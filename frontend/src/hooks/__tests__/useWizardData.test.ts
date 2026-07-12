import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { useWizardData } = await import("../useWizardData");

const ok = (body: unknown) => ({
  ok: true,
  status: 200,
  json: () => Promise.resolve(body),
});

describe("useWizardData", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn((input: string | URL | Request) => {
      const path = new URL(String(input)).pathname;
      if (path === "/api/v1/presets/constellations") {
        return Promise.resolve(ok({
          presets: [
            {
              name: "luna-polar-2",
              description: "Lunar polar",
              satellite_count: 2,
              constellation: "nodalarc:constellations/luna/llo/luna-polar-2.yaml",
              ground_stations: "nodalarc:site-sets/luna/luna-surface-sites.yaml",
              default_node: "luna-relay",
              capability: {
                source_kind: "constellation",
                runtime_supported_propagators: ["j2_mean_elements", "two_body"],
                default_propagator: "two_body",
                unavailable_reason: null,
              },
            },
          ],
          custom_geometry: {
            source_kind: "custom_geometry",
            runtime_supported_propagators: ["j2_mean_elements", "two_body"],
            default_propagator: "j2_mean_elements",
            unavailable_reason: null,
          },
          custom_geometry_seed: {
            display_name: "Custom 4x11 shell",
            description: "backend seed",
            altitude_km: 550,
            inclination_deg: 53,
            pattern: "walker_delta",
            planes: 4,
            slots_per_plane: 11,
            raan_spacing_deg: 90,
            phase_offset_deg: 8.182,
          },
          custom_geometry_default_node: "nodalarc:nodes/space/starlink-v2-mesh.yaml",
          custom_geometry_patterns: [
            { id: "walker_delta", label: "Delta", description: "Delta pattern" },
            { id: "walker_star", label: "Star", description: "Star pattern" },
          ],
          orbit_models: [
            { id: "j2_mean_elements", label: "J2", description: "J2" },
            { id: "two_body", label: "Two body", description: "Two body" },
            { id: "sgp4_tle", label: "SGP4", description: "SGP4" },
          ],
        }));
      }
      if (path === "/api/v1/wizard/extensions") {
        return Promise.resolve(ok({
          protocols: [
            {
              id: "isis",
              label: "IS-IS",
              description: "IS-IS",
              extensions: ["te"],
              extension_constraints: {},
              timer_label: "IS-IS Timers",
              timer_fields: [],
              non_flat_area_warning: null,
            },
            {
              id: "ospf",
              label: "OSPF",
              description: "OSPF",
              extensions: ["te"],
              extension_constraints: {},
              timer_label: "OSPF Timers",
              timer_fields: [],
              non_flat_area_warning: "warning",
            },
          ],
          extensions: [{ id: "te", label: "TE", description: "Traffic engineering" }],
          area_strategies: ["flat"],
          default_area_strategy: "flat",
          bfd: {
            heading: "BFD",
            enabled_field: "bfd",
            enable_label: "Enable BFD",
            enable_description: "Detect failures",
            timer_fields: [
              { id: "bfd_detect_multiplier", label: "Multiplier", unit: null, description: "Multiplier", guidance: "Three", minimum: 1 },
              { id: "bfd_rx_interval", label: "RX", unit: "ms", description: "Receive", guidance: "300", minimum: 1 },
              { id: "bfd_tx_interval", label: "TX", unit: "ms", description: "Transmit", guidance: "300", minimum: 1 },
            ],
          },
          routing_timer_defaults: {
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
        }));
      }
      if (path === "/api/v1/presets/satellite-types") {
        return Promise.resolve(ok({ presets: [] }));
      }
      if (path === "/api/v1/presets/ground-stations") {
        return Promise.resolve(ok({ presets: [] }));
      }
      if (path === "/api/v1/presets/ground-stations/stations") {
        return Promise.resolve(ok({ stations: [] }));
      }
      return Promise.resolve(ok([]));
    }) as unknown as typeof fetch;
  });

  it("preserves backend-owned preset and custom-geometry capabilities", async () => {
    const { result } = renderHook(() => useWizardData());

    await waitFor(() => expect(result.current.presets).toHaveLength(1));
    expect(result.current.presets[0]!.capability.default_propagator).toBe("two_body");
    expect(result.current.customConstellationCapability?.source_kind).toBe(
      "custom_geometry",
    );
    expect(
      result.current.customConstellationCapability?.runtime_supported_propagators,
    ).toEqual(["j2_mean_elements", "two_body"]);
    expect(result.current.customConstellationSeed?.planes).toBe(4);
    expect(result.current.customConstellationPatterns.map((pattern) => pattern.id)).toEqual([
      "walker_delta",
      "walker_star",
    ]);
    expect(result.current.orbitModels.map((model) => model.id)).toEqual([
      "j2_mean_elements",
      "two_body",
      "sgp4_tle",
    ]);
  });
});
