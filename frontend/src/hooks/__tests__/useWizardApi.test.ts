// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { WizardRuntimeState } from "../../catalog/wizardTypes";

const { writeSessionYamlExport, downloadBlob } = vi.hoisted(() => ({
  writeSessionYamlExport: vi.fn(() => Promise.resolve()),
  downloadBlob: vi.fn(),
}));

vi.mock("../../builder/sessionYamlTransfer", () => ({ writeSessionYamlExport }));
vi.mock("../../ui/downloadBlob", () => ({ downloadBlob }));

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
      default_node: "starlink-v2-mesh",
      capability: {
        source_kind: "constellation",
        runtime_supported_propagators: ["j2_mean_elements", "two_body"],
        default_propagator: "j2_mean_elements",
        unavailable_reason: null,
      },
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
    writeSessionYamlExport.mockClear();
    downloadBlob.mockClear();
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        draft: {
          contract_version: 1,
          draft_revision: 1,
          state: { session: { session: { name: "wizard-test" } }, catalog_documents: [] },
        },
        target_ref: "user:sessions/wizard/wizard-test.yaml",
        canonical_session_yaml: "session:\n  name: wizard-test\n",
        save_verdict: { operation: "save", allowed: true, blockers: [] },
        deploy_eligibility_after_save: { operation: "deploy", allowed: true, blockers: [] },
      }),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  it("sends the selected orbit propagator when generating a session", async () => {
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });

    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    expect(fetchMock.mock.calls[0]![0]).toBe("http://test:8080/api/v1/builder/wizard/compile");
    const intent = body.intent as Record<string, unknown>;
    expect(intent.orbit_propagator).toBe("j2_mean_elements");
    expect(intent.constellation_ref).toBe(
      "nodalarc:constellations/earth/leo/earth-leo-walker-delta-176.yaml",
    );
    expect(intent.protocol).toBe("isis");
    expect(intent.extensions).toEqual(["te", "mpls"]);
    expect(intent.area_strategy).toBe("per_plane");
  });

  it("sends raw typed timer intent and never constructs routing grammar", async () => {
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });

    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    const intent = body.intent as Record<string, unknown>;
    expect(intent.routing_config).toBeUndefined();
    expect(intent.timers).toBeUndefined();
    expect(intent.routing_timers).toEqual(wizardState().routingTimers);
  });

  it("sends OSPF panel values without mapping them client-side", async () => {
    const { result } = renderHook(() => useWizardApi());
    const state = wizardState();
    state.protocol = "ospf";
    state.routingTimers = {
      ...state.routingTimers!,
      ospf_hello_interval: 2,
      ospf_dead_interval: 8,
      bfd: true,
    };

    await act(async () => { await result.current.generate(state); });

    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string) as Record<string, unknown>;
    const timers = (body.intent as Record<string, unknown>).routing_timers as Record<string, unknown>;
    expect(timers.ospf_hello_interval).toBe(2);
    expect(timers.ospf_dead_interval).toBe(8);
    expect(timers.bfd).toBe(true);
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

  it("sends typed physical intent for custom coverage preview", async () => {
    const state = wizardState();
    state.constellation = {
      ...state.constellation!,
      constellation: null,
      custom_geometry: {
        display_name: "Custom shell",
        description: "Typed geometry",
        altitude_km: 550,
        inclination_deg: 53,
        pattern: "walker_delta",
        planes: 2,
        slots_per_plane: 3,
        raan_spacing_deg: 180,
        phase_offset_deg: 60,
      },
    };
    state.groundStationSet = {
      name: "custom",
      description: "Typed site selection",
      stations: ["hawthorne"],
      file: null,
      custom_site_refs: ["nodalarc:sites/earth/us/earth-us-hawthorne.yaml"],
    };
    const coverage = {
      orbital_period_s: 5_700,
      preview_step_s: 60,
      isl: {
        total_possible: 6,
        formed_at_least_once: 6,
        never_formed: 0,
        feasibility_pct: 100,
        min_active: 4,
        max_active: 6,
        failure_reasons: null,
      },
      ground_stations: {
        per_station: {
          hawthorne: { coverage_pct: 42, longest_gap_s: 600, reason: null },
        },
        simultaneous_min: 0,
        simultaneous_max: 1,
        simultaneous_mean: 0.42,
        max_gap_s: 600,
      },
      warnings: [{ severity: "info", message: "Expected orbital gaps" }],
    };
    fetchMock.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(coverage) });
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.previewCoverage(state); });

    expect(fetchMock.mock.calls[0]![0]).toBe(
      "http://test:8080/api/v1/session/preview-coverage",
    );
    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string);
    expect(body.intent.constellation_ref).toBeNull();
    expect(body.intent.custom_constellation).toEqual(state.constellation!.custom_geometry);
    expect(body.intent.custom_site_refs).toEqual(state.groundStationSet!.custom_site_refs);
    expect(JSON.stringify(body)).not.toContain('"constellation":{"constellation"');
    expect(result.current.coveragePreview).toEqual(coverage);
  });

  it("saves then deploys the exact backend-reviewed revision and closure", async () => {
    const compiled = {
      draft: {
        contract_version: 1,
        draft_revision: 1,
        state: { session: { session: { name: "wizard-test" } }, catalog_documents: [] },
      },
      target_ref: "user:sessions/wizard/wizard-test.yaml",
      canonical_session_yaml: "session:\n  name: wizard-test\n",
      save_verdict: { operation: "save", allowed: true, blockers: [] },
      deploy_eligibility_after_save: { operation: "deploy", allowed: true, blockers: [] },
    };
    const saved = {
      session: {
        ref: compiled.target_ref,
        revision: "revision-1",
        canonical_yaml: compiled.canonical_session_yaml,
      },
      digests: {
        document: `sha256:${"a".repeat(64)}`,
        dependency: `sha256:${"b".repeat(64)}`,
      },
      deploy_verdict: { allowed: true, blockers: [] },
    };
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(compiled) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(saved) })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: "accepted", operation_id: "operation-1" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: "accepted", operation_id: "operation-2" }),
      });
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });
    let deployed = false;
    await act(async () => { deployed = await result.current.deploy(); });
    let redeployed = false;
    await act(async () => { redeployed = await result.current.deploy(); });

    expect(deployed).toBe(true);
    expect(redeployed).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://test:8080/api/v1/builder/wizard/compile",
      "http://test:8080/api/v1/builder/session/save",
      "http://test:8080/api/v1/builder/session/deploy",
      "http://test:8080/api/v1/builder/session/deploy",
    ]);
    const deployBody = JSON.parse(fetchMock.mock.calls[2]![1]!.body as string);
    expect(deployBody).toEqual({
      session_ref: compiled.target_ref,
      expected_session_revision: "revision-1",
      expected_document_digest: saved.digests.document,
      expected_dependency_digest: saved.digests.dependency,
    });
    expect(JSON.parse(fetchMock.mock.calls[3]![1]!.body as string)).toEqual(deployBody);
  });

  it("downloads a proposal-free Wizard session as one ordinary YAML file", async () => {
    const compiled = {
      draft: {
        contract_version: 1,
        draft_revision: 1,
        state: { session: { session: { name: "wizard-test" } }, catalog_documents: [] },
      },
      target_ref: "user:sessions/wizard/wizard-test.yaml",
      canonical_session_yaml: "session:\n  name: wizard-test\n",
      save_verdict: { operation: "save", allowed: true, blockers: [] },
      deploy_eligibility_after_save: { operation: "deploy", allowed: true, blockers: [] },
    };
    fetchMock.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(compiled) });
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });
    let exported = false;
    await act(async () => { exported = await result.current.exportYaml(); });

    expect(exported).toBe(true);
    expect(downloadBlob).toHaveBeenCalledWith(
      compiled.canonical_session_yaml,
      "wizard-test.yaml",
    );
    expect(writeSessionYamlExport).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://test:8080/api/v1/builder/wizard/compile",
    ]);
  });

  it("saves proposal-bearing Wizard output before exporting its ordinary YAML files", async () => {
    const compiled = {
      draft: {
        contract_version: 1,
        draft_revision: 1,
        state: {
          session: { session: { name: "wizard-custom" } },
          catalog_documents: [
            {
              ref: "user:orbits/wizard/custom-orbit.yaml",
              document: { orbit: { id: "custom-orbit" } },
              origin: "generated",
            },
          ],
        },
      },
      target_ref: "user:sessions/wizard/wizard-custom.yaml",
      canonical_session_yaml: "session:\n  name: wizard-custom\n",
      save_verdict: { operation: "save", allowed: true, blockers: [] },
      deploy_eligibility_after_save: { operation: "deploy", allowed: true, blockers: [] },
    };
    const saved = {
      session: {
        ref: compiled.target_ref,
        revision: "revision-custom",
        canonical_yaml: compiled.canonical_session_yaml,
      },
      digests: {
        document: `sha256:${"a".repeat(64)}`,
        dependency: `sha256:${"b".repeat(64)}`,
      },
      deploy_verdict: { allowed: true, blockers: [] },
    };
    const yamlExport = {
      session_ref: compiled.target_ref,
      session_revision: saved.session.revision,
      files: [
        { logical_path: "session.yaml", yaml_text: compiled.canonical_session_yaml },
        {
          logical_path: "catalog/user/orbits/wizard/custom-orbit.yaml",
          yaml_text: "orbit:\n  id: custom-orbit\n",
        },
      ],
    };
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(compiled) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(saved) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(yamlExport) });
    const { result } = renderHook(() => useWizardApi());

    await act(async () => { await result.current.generate(wizardState()); });
    let exported = false;
    await act(async () => { exported = await result.current.exportYaml(); });

    expect(exported).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://test:8080/api/v1/builder/wizard/compile",
      "http://test:8080/api/v1/builder/session/save",
      "http://test:8080/api/v1/builder/session/yaml/export",
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1]![1]!.body as string)).toEqual({
      draft: compiled.draft,
      target_ref: compiled.target_ref,
    });
    expect(JSON.parse(fetchMock.mock.calls[2]![1]!.body as string)).toEqual({
      session_ref: saved.session.ref,
      expected_session_revision: saved.session.revision,
    });
    expect(writeSessionYamlExport).toHaveBeenCalledWith(
      yamlExport.session_ref,
      yamlExport.files,
    );
    expect(downloadBlob).not.toHaveBeenCalled();
  });
});
