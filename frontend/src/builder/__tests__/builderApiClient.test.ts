import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const {
  BuilderApiError,
  addCatalogDraftNodeEthernet,
  addCatalogDraftNodeTerminal,
  addCatalogDraftSiteNode,
  applyVisualDraftCommand,
  compileCatalogDraft,
  compileVisualDraft,
  createCatalogDraft,
  createVisualDraft,
  customizeVisualDraftChain,
  deployBuilderSession,
  deriveVisualWalkerLayout,
  exportCatalogSession,
  getBuilderBootstrap,
  getSessionTransition,
  getWizardAvailableStations,
  getWizardConstellationPresets,
  getWizardExtensionRules,
  getWizardGroundStationSets,
  getWizardSatelliteTypes,
  importCatalogSession,
  listCatalog,
  openCatalogDraft,
  openVisualDraft,
  patchCatalogDraft,
  previewWizardCoverage,
  replaceCatalogDraftObject,
  saveCatalogDraft,
  saveBuilderSession,
} = await import("../builderApiClient");

const ok = (body: unknown) => ({
  ok: true,
  status: 200,
  json: () => Promise.resolve(body),
});

describe("typed Builder API client", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(() => Promise.resolve(ok({}))) as unknown as typeof fetch;
  });

  it("uses the generated visual draft routes without a local grammar envelope", async () => {
    const draft = {
      contract_version: 1 as const,
      draft_revision: 9,
      mode: "opaque_yaml" as const,
      target_ref: "user:sessions/typed.yaml",
      session_name_is_placeholder: false,
      reserved_authoring_ids: [],
      session_yaml: "session:\n  name: typed\n",
    };

    await createVisualDraft({ session_name: "typed" });
    await openVisualDraft({ source_ref: "nodalarc:sessions/typed.yaml" });
    await compileVisualDraft({ draft });
    await applyVisualDraftCommand({
      draft,
      expected_draft_revision: 9,
      command: { operation: "add_generated_space", phasing_mode: "walker_delta" },
    });
    await deriveVisualWalkerLayout({
      pattern: "walker_delta",
      planes: 4,
      slots_per_plane: 11,
    });
    await customizeVisualDraftChain({
      draft,
      segment_id: "space",
      leaf_ref: "nodalarc:nodes/space/typed.yaml",
    });

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://test:8080/api/v1/builder/draft/new",
      "http://test:8080/api/v1/builder/draft/open",
      "http://test:8080/api/v1/builder/draft/compile",
      "http://test:8080/api/v1/builder/draft/command",
      "http://test:8080/api/v1/builder/defaults/walker-layout",
      "http://test:8080/api/v1/builder/draft/customize-chain",
    ]);
    expect(JSON.parse(fetchMock.mock.calls[2]![1].body)).toEqual({ draft });
    expect(JSON.parse(fetchMock.mock.calls[3]![1].body)).toEqual({
      draft,
      expected_draft_revision: 9,
      command: { operation: "add_generated_space", phasing_mode: "walker_delta" },
    });
    expect(JSON.parse(fetchMock.mock.calls[4]![1].body)).toEqual({
      pattern: "walker_delta",
      planes: 4,
      slots_per_plane: 11,
    });
  });

  it("sends only saved revision and digest fences to deploy", async () => {
    const request = {
      session_ref: "user:sessions/typed.yaml",
      expected_session_revision: "revision-1",
      expected_document_digest: "sha256:document",
      expected_dependency_digest: "sha256:dependency",
    };

    await deployBuilderSession(request);

    const body = JSON.parse(
      (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]![1].body,
    );
    expect(body).toEqual(request);
    expect(body).not.toHaveProperty("scope");
    expect(body).not.toHaveProperty("path");
    expect(body).not.toHaveProperty("upload");
  });

  it("uses closed typed bodies for save and paged catalog list", async () => {
    const save = {
      draft: {
        contract_version: 1 as const,
        draft_revision: 1,
        state: { session: { session: { name: "typed" } } },
      },
      target_ref: "user:sessions/typed.yaml",
      expected_session_revision: "revision-1",
    };
    await saveBuilderSession(save);
    await listCatalog({ family: "nodes", page_size: 100, page_token: "opaque" });

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls[0]![0]).toBe(
      "http://test:8080/api/v1/builder/session/save",
    );
    expect(fetchMock.mock.calls[1]![0]).toBe(
      "http://test:8080/api/v1/builder/catalog/list",
    );
  });

  it("uses the typed Wizard coverage contract outside the Builder route prefix", async () => {
    const request = {
      intent: {
        constellation_ref: "nodalarc:constellations/earth/leo/example.yaml",
        ground_site_set_ref: "nodalarc:site-sets/earth/leo/example.yaml",
        orbit_propagator: "j2_mean_elements" as const,
      },
    };

    await previewWizardCoverage(request);

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls[0]![0]).toBe(
      "http://test:8080/api/v1/session/preview-coverage",
    );
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual(request);
  });

  it("loads the closed Wizard constellation capability response", async () => {
    await getWizardConstellationPresets();
    await getWizardSatelliteTypes();
    await getWizardGroundStationSets();
    await getWizardAvailableStations();
    await getWizardExtensionRules();

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://test:8080/api/v1/presets/constellations",
      "http://test:8080/api/v1/presets/satellite-types",
      "http://test:8080/api/v1/presets/ground-stations",
      "http://test:8080/api/v1/presets/ground-stations/stations",
      "http://test:8080/api/v1/wizard/extensions",
    ]);
    expect(fetchMock.mock.calls.every((call) => call[1].method === "GET")).toBe(true);
  });

  it("reads path-free typed transition proof by encoded operation identity", async () => {
    await getSessionTransition("operation with spaces");

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls[0]![0]).toBe(
      "http://test:8080/api/v1/session-transitions/operation%20with%20spaces",
    );
    expect(fetchMock.mock.calls[0]![1].method).toBe("GET");
  });

  it("uses backend-owned component drafts and exact-closure transfer routes", async () => {
    const draft = {
      contract_version: 1 as const,
      draft_revision: 0,
      family: "terminals" as const,
      target_ref: "user:terminals/my-ka.yaml",
      source_ref: "nodalarc:terminals/ka.yaml",
      expected_source_revision: "revision-source",
      document: { terminal: { id: "my-ka", display_name: "My Ka" } },
      issues: [],
    };
    await getBuilderBootstrap();
    await createCatalogDraft({ family: "nodes", object_id: "my-node" });
    await openCatalogDraft({
      source_ref: "nodalarc:terminals/ka.yaml",
      target_ref: "user:terminals/my-ka.yaml",
    });
    await patchCatalogDraft({
      draft,
      expected_draft_revision: 0,
      commands: [{ operation: "replace", pointer: "/terminal/display_name", value: "Edited" }],
    });
    await addCatalogDraftSiteNode({
      draft: {
        ...draft,
        family: "sites",
        target_ref: "user:sites/test-site.yaml",
        source_ref: "nodalarc:sites/test-site.yaml",
        document: { site: { id: "test-site" } },
      },
      expected_draft_revision: 0,
      node_id: "gw-explicit",
      node_ref: "nodalarc:nodes/ground/gateway.yaml",
    });
    const nodeDraft = {
      ...draft,
      family: "nodes" as const,
      target_ref: "user:nodes/test-node.yaml",
      source_ref: "nodalarc:nodes/test-node.yaml",
      document: { node: { id: "test-node" } },
    };
    await addCatalogDraftNodeTerminal({
      draft: nodeDraft,
      expected_draft_revision: 0,
      terminal_ref: "nodalarc:terminals/rf/selected.yaml",
      role: "access",
    });
    await addCatalogDraftNodeEthernet({
      draft: nodeDraft,
      expected_draft_revision: 0,
    });
    await replaceCatalogDraftObject({
      draft,
      expected_draft_revision: 0,
      raw_object_json: '{"id":"my-ka","display_name":"Advanced"}',
    });
    await compileCatalogDraft({ draft, expected_draft_revision: 0 });
    await saveCatalogDraft({ draft, expected_draft_revision: 0 });
    await exportCatalogSession({
      session_ref: "user:sessions/test.yaml",
      expected_session_revision: "revision-session",
    });
    await importCatalogSession({
      contract_version: 1,
      root_ref: "user:sessions/test.yaml",
      root_yaml: "session: {}\n",
      document_digest: "document-digest",
      closure_digest: "closure-digest",
      entries: [],
      commit: false,
    });

    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://test:8080/api/v1/builder/bootstrap",
      "http://test:8080/api/v1/builder/catalog/draft/new",
      "http://test:8080/api/v1/builder/catalog/draft/open",
      "http://test:8080/api/v1/builder/catalog/draft/patch",
      "http://test:8080/api/v1/builder/catalog/draft/site-node/add",
      "http://test:8080/api/v1/builder/catalog/draft/node-terminal/add",
      "http://test:8080/api/v1/builder/catalog/draft/node-ethernet/add",
      "http://test:8080/api/v1/builder/catalog/draft/replace-object",
      "http://test:8080/api/v1/builder/catalog/draft/compile",
      "http://test:8080/api/v1/builder/catalog/draft/save",
      "http://test:8080/api/v1/builder/session/export",
      "http://test:8080/api/v1/builder/session/import",
    ]);
  });

  it("preserves typed refusal evidence, including committed save state", async () => {
    const detail = {
      code: "builder_session_save.storage_verification_failed",
      message: "post-commit readback failed",
      target_ref: "user:sessions/typed.yaml",
      repository_committed: true,
    };
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve(detail),
      }),
    ) as unknown as typeof fetch;

    await expect(
      saveBuilderSession({
        draft: {
          contract_version: 1,
          draft_revision: 1,
          state: { session: { session: { name: "typed" } } },
        },
        target_ref: "user:sessions/typed.yaml",
      }),
    ).rejects.toMatchObject({
      name: "BuilderApiError",
      status: 500,
      detail,
      message: "post-commit readback failed",
    } satisfies Partial<InstanceType<typeof BuilderApiError>>);
  });
});
