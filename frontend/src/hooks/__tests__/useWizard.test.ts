import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const clearYaml = vi.fn();
const clearError = vi.fn();
let constraints: Record<string, string[]> = {};

vi.mock("../useWizardData", () => ({
  useWizardData: () => ({
    presets: [],
    customConstellationCapability: null,
    customConstellationSeed: null,
    customConstellationDefaultNode: null,
    customConstellationPatterns: [],
    orbitModels: [],
    satelliteTypes: [],
    groundStationSets: [],
    availableStations: [],
    rules: {
      protocols: [
        {
          id: "isis",
          label: "IS-IS",
          description: "IS-IS",
          extensions: ["sr", "te", "mpls"],
          extension_constraints: constraints,
          timer_label: "IS-IS Timers",
          timer_fields: [],
          non_flat_area_warning: null,
        },
        {
          id: "ospf",
          label: "OSPF",
          description: "OSPF",
          extensions: ["sr", "te", "mpls"],
          extension_constraints: constraints,
          timer_label: "OSPF Timers",
          timer_fields: [],
          non_flat_area_warning: "warning",
        },
      ],
      extensions: [
        { id: "sr", label: "SR", description: "Segment routing" },
        { id: "te", label: "TE", description: "Traffic engineering" },
        { id: "mpls", label: "MPLS", description: "MPLS" },
      ],
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
    },
  }),
}));

vi.mock("../useWizardApi", () => ({
  useWizardApi: () => ({
    generating: false,
    deploying: false,
    previewing: false,
    exporting: false,
    generatedYaml: null,
    coveragePreview: null,
    error: null,
    clearError,
    clearYaml,
    clearPreview: vi.fn(),
    generate: vi.fn(),
    deploy: vi.fn(),
    exportYaml: vi.fn(),
    deployUploadedYaml: vi.fn(),
    previewCoverage: vi.fn(),
  }),
}));

const { useWizard } = await import("../useWizard");

beforeEach(() => {
  constraints = {};
  clearYaml.mockClear();
  clearError.mockClear();
});

describe("useWizard extension authority", () => {
  it("does not invent an MPLS to TE dependency", () => {
    const { result } = renderHook(() => useWizard());

    act(() => result.current.selectProtocol("isis"));
    act(() => result.current.toggleExtension("mpls"));

    expect(result.current.state.extensions).toEqual(["mpls"]);
  });

  it("reconciles selected extensions from backend dependency rules", () => {
    constraints = { mpls: ["sr"] };
    const { result } = renderHook(() => useWizard());

    act(() => result.current.selectProtocol("isis"));
    act(() => result.current.toggleExtension("sr"));
    act(() => result.current.toggleExtension("mpls"));
    expect(result.current.state.extensions).toEqual(["sr", "mpls"]);

    act(() => result.current.toggleExtension("sr"));
    expect(result.current.state.extensions).toEqual([]);
  });
});
