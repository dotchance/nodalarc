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
    orbitModels: [],
    satelliteTypes: [],
    groundStationSets: [],
    availableStations: [],
    rules: {
      protocols: {
        isis: { extensions: ["sr", "te", "mpls"], constraints },
        ospf: { extensions: ["sr", "te", "mpls"], constraints },
      },
      area_strategies: ["flat"],
      available_protocols: ["isis", "ospf"],
      default_area_strategy: "flat",
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
    exportClosure: vi.fn(),
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
