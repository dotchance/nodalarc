import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CatalogComponentDraftEnvelope,
  CatalogComponentFamily,
  CatalogDraftAddNodeEthernetPortRequest,
  CatalogDraftAddNodeTerminalMountRequest,
  CatalogDraftAddSiteNodeRequest,
  CatalogDraftPatchRequest,
  CatalogDraftReplaceObjectRequest,
  CatalogDraftSaveResult,
  CatalogFamilyMetadata,
  JsonValue,
} from "../generated/builderApi";
import type { CatalogDraftEditorRecovery } from "../structuredDraftRecovery";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";

const mocks = vi.hoisted(() => ({
  addCatalogDraftNodeEthernet: vi.fn(),
  addCatalogDraftNodeTerminal: vi.fn(),
  addCatalogDraftSiteNode: vi.fn(),
  patchCatalogDraft: vi.fn(),
  replaceCatalogDraftObject: vi.fn(),
  compileCatalogDraft: vi.fn(),
  saveCatalogDraft: vi.fn(),
  getCatalogDependents: vi.fn(),
}));
const {
  addCatalogDraftNodeEthernet,
  addCatalogDraftNodeTerminal,
  addCatalogDraftSiteNode,
  patchCatalogDraft,
  replaceCatalogDraftObject,
  compileCatalogDraft,
  saveCatalogDraft,
  getCatalogDependents,
} = mocks;

vi.mock("../builderApiClient", () => ({
  ...mocks,
}));

vi.mock("../useBuilderWorld", () => ({
  useBuilderCatalog: (family: string) => ({
    entries: family === "nodes"
      ? [
          { ref: "nodalarc:nodes/ground/first.yaml", display_name: "First model" },
          { ref: "nodalarc:nodes/ground/selected.yaml", display_name: "Selected model" },
        ]
      : family === "terminals"
        ? [
            {
              ref: "nodalarc:terminals/rf/selected.yaml",
              namespace: "nodalarc",
              display_name: "Selected terminal",
            },
          ]
      : [],
    error: null,
    refresh: () => Promise.resolve(),
  }),
}));

const { CatalogDraftEditorWindow: CatalogDraftEditorWindowBase, catalogDraftFieldCommands } = await import(
  "../CatalogDraftEditorWindow"
);

function CatalogDraftEditorWindow(
  props: Omit<ComponentProps<typeof CatalogDraftEditorWindowBase>, "authoring">,
) {
  return <CatalogDraftEditorWindowBase {...props} authoring={AUTHORING_FACTS} />;
}

const WRAPPERS: Readonly<Record<CatalogComponentFamily, string>> = {
  bodies: "body",
  terminals: "terminal",
  payloads: "payload",
  orbits: "orbit",
  nodes: "node",
  sites: "site",
  "site-sets": "site_set",
  constellations: "constellation",
  "space-node-sets": "space_node_set",
};

function metadata(family: CatalogComponentFamily): CatalogFamilyMetadata {
  return {
    family,
    wrapper: WRAPPERS[family],
    direct_user_write: true,
    component_fork: true,
    session_draft_save: false,
  };
}

function draft(
  family: CatalogComponentFamily,
  object: Readonly<Record<string, JsonValue>> = {},
  options: {
    revision?: number;
    expectedTargetRevision?: string | null;
    issues?: CatalogComponentDraftEnvelope["issues"];
  } = {},
): CatalogComponentDraftEnvelope {
  const wrapper = WRAPPERS[family];
  const objectId = `test-${family.replace(/s$/, "")}`;
  return {
    contract_version: 1,
    draft_revision: options.revision ?? 0,
    family,
    target_ref: `user:${family}/${objectId}.yaml`,
    source_ref: `nodalarc:${family}/source.yaml`,
    expected_source_revision: "revision-source",
    expected_target_revision: options.expectedTargetRevision ?? null,
    document: {
      [wrapper]: {
        id: objectId,
        display_name: `Test ${family}`,
        ...object,
      },
    },
    issues: options.issues ?? [],
  };
}

function decodePointer(pointer: string): string[] {
  return pointer
    .split("/")
    .slice(1)
    .map((token) => token.replace(/~1/g, "/").replace(/~0/g, "~"));
}

function applyPatch(request: CatalogDraftPatchRequest): CatalogComponentDraftEnvelope {
  const document = structuredClone(request.draft.document) as Record<string, unknown>;
  for (const command of request.commands) {
    const tokens = decodePointer(command.pointer);
    let parent: Record<string, unknown> | unknown[] = document;
    for (const token of tokens.slice(0, -1)) {
      parent = Array.isArray(parent)
        ? parent[Number(token)] as Record<string, unknown> | unknown[]
        : parent[token] as Record<string, unknown> | unknown[];
    }
    const final = tokens[tokens.length - 1]!;
    if (Array.isArray(parent)) {
      if (command.operation === "remove") parent.splice(Number(final), 1);
      else parent[Number(final)] = structuredClone(command.value);
    } else if (command.operation === "remove") {
      delete parent[final];
    } else {
      parent[final] = structuredClone(command.value);
    }
  }
  return {
    ...request.draft,
    draft_revision: request.draft.draft_revision + 1,
    document: document as Readonly<Record<string, JsonValue>>,
    issues: [],
  };
}

function validatedCanonicalDocument(
  document: Readonly<Record<string, JsonValue>>,
): Readonly<Record<string, JsonValue>> {
  return document;
}

function saveResult(
  input: CatalogComponentDraftEnvelope,
  revision = "revision-saved",
): CatalogDraftSaveResult {
  const savedDraft: CatalogComponentDraftEnvelope = {
    ...input,
    draft_revision: input.draft_revision + 1,
    expected_source_revision: revision,
    expected_target_revision: revision,
  };
  const document = {
    ref: input.target_ref,
    family: input.family,
    canonical_yaml: "saved: true\n",
    canonical_json: validatedCanonicalDocument(input.document),
    content_digest: "digest-saved",
    revision,
  };
  const impact = {
    target_ref: input.target_ref,
    target_revision: revision,
    direct_dependents: [],
    transitive_dependents: [],
    overwrite_affects_dependents: false,
    delete_allowed: true,
    acknowledgement: "impact-saved",
  };
  return {
    draft: savedDraft,
    result: { document, impact },
    compile_result: {
      draft: savedDraft,
      save_allowed: true,
      runtime_supported: true,
      canonical_yaml: document.canonical_yaml,
      canonical_json: validatedCanonicalDocument(input.document),
      content_digest: document.content_digest,
      issues: [],
    },
  };
}

function installHappyPath() {
  addCatalogDraftNodeTerminal.mockImplementation(
    async (request: CatalogDraftAddNodeTerminalMountRequest) => {
      const document = structuredClone(request.draft.document) as Record<string, any>;
      document.node.terminals = [
        ...(document.node.terminals ?? []),
        {
          id: `${request.role}_0`,
          role: request.role,
          terminal: request.terminal_ref,
          count: 1,
          ...(request.role === "access" ? { boresight: { mode: "nadir" } } : {}),
        },
      ];
      return {
        ...request.draft,
        draft_revision: request.draft.draft_revision + 1,
        document,
        issues: [],
      };
    },
  );
  addCatalogDraftNodeEthernet.mockImplementation(
    async (request: CatalogDraftAddNodeEthernetPortRequest) => {
      const document = structuredClone(request.draft.document) as Record<string, any>;
      document.node.ethernet = [...(document.node.ethernet ?? []), { id: "terr0" }];
      return {
        ...request.draft,
        draft_revision: request.draft.draft_revision + 1,
        document,
        issues: [],
      };
    },
  );
  addCatalogDraftSiteNode.mockImplementation(
    async (request: CatalogDraftAddSiteNodeRequest) => {
      const document = structuredClone(request.draft.document) as Record<string, any>;
      document.site.nodes = [
        ...(document.site.nodes ?? []),
        {
          id: request.node_id,
          model: request.node_ref,
          payloads: {},
          terminals: {
            access: {
              installed_count: 2,
              capabilities: { boresight: { mode: "local_vertical" } },
            },
          },
          interfaces: { lo0: { ipv4: "" }, terr0: { ipv4: "" } },
        },
      ];
      return {
        ...request.draft,
        draft_revision: request.draft.draft_revision + 1,
        document,
        issues: [],
      };
    },
  );
  patchCatalogDraft.mockImplementation(async (request: CatalogDraftPatchRequest) =>
    applyPatch(request),
  );
  replaceCatalogDraftObject.mockImplementation(
    async (request: CatalogDraftReplaceObjectRequest) => {
      const wrapper = WRAPPERS[request.draft.family];
      return {
        ...request.draft,
        draft_revision: request.draft.draft_revision + 1,
        document: {
          ...request.draft.document,
          [wrapper]: JSON.parse(request.raw_object_json) as JsonValue,
        },
        issues: [],
      };
    },
  );
  compileCatalogDraft.mockImplementation(async ({ draft: value }) => ({
    draft: value,
    save_allowed: true,
    runtime_supported: true,
    canonical_yaml: "compiled: true\n",
    canonical_json: validatedCanonicalDocument(value.document),
    content_digest: "digest-compiled",
    issues: [],
  }));
  saveCatalogDraft.mockImplementation(async ({ draft: value }) => saveResult(value));
  getCatalogDependents.mockResolvedValue({
    target_ref: "user:terminals/test.yaml",
    target_revision: "revision-current",
    direct_dependents: [],
    transitive_dependents: [],
    overwrite_affects_dependents: false,
    delete_allowed: true,
    acknowledgement: "impact-current",
  });
}

async function advancedTextarea(): Promise<HTMLTextAreaElement> {
  const textarea = screen.getByPlaceholderText("catalog component JSON") as HTMLTextAreaElement;
  await waitFor(() => expect(textarea.value).toContain('"id"'));
  return textarea;
}

beforeEach(() => {
  vi.clearAllMocks();
  installHappyPath();
});

afterEach(cleanup);

describe("CatalogDraftEditorWindow", () => {
  it("sends node creation intent and adopts backend-generated mount and port fields", async () => {
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("nodes", {
          forwarding: "routed",
          ethernet: [],
          terminals: [],
          payloads: [],
        })}
        metadata={metadata("nodes")}
        onSaved={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("new mount terminal"), {
      target: { value: "nodalarc:terminals/rf/selected.yaml" },
    });
    fireEvent.click(screen.getByRole("button", { name: "+ terminal mount" }));
    await waitFor(() => expect(addCatalogDraftNodeTerminal).toHaveBeenCalledTimes(1));
    expect(addCatalogDraftNodeTerminal.mock.calls[0]![0]).toMatchObject({
      expected_draft_revision: 0,
      terminal_ref: "nodalarc:terminals/rf/selected.yaml",
      role: "access",
    });
    expect(addCatalogDraftNodeTerminal.mock.calls[0]![0]).not.toHaveProperty("id");
    expect(addCatalogDraftNodeTerminal.mock.calls[0]![0]).not.toHaveProperty("count");
    expect(addCatalogDraftNodeTerminal.mock.calls[0]![0]).not.toHaveProperty("boresight");
    expect(await screen.findByText("access_0")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "+ LAN port" }));
    await waitFor(() => expect(addCatalogDraftNodeEthernet).toHaveBeenCalledTimes(1));
    expect(addCatalogDraftNodeEthernet.mock.calls[0]![0]).toMatchObject({
      expected_draft_revision: 1,
    });
    expect(addCatalogDraftNodeEthernet.mock.calls[0]![0]).not.toHaveProperty("port_id");
    expect((await screen.findByLabelText("LAN port") as HTMLInputElement).value).toBe("terr0");
  });

  it("requires explicit site-node intent and adopts the backend-derived document", async () => {
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("sites", {
          lan: { ipv4: "172.30.0.0/24" },
          nodes: [],
          frame: { body_fixed: { body: "nodalarc:bodies/earth.yaml" } },
          location: { lat_deg: 0, lon_deg: 0, alt_m: 0 },
        })}
        metadata={metadata("sites")}
        onSaved={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const addButton = screen.getByRole("button", { name: "+ add node" }) as HTMLButtonElement;
    expect(addButton.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("node id"), {
      target: { value: "edge-router" },
    });
    expect(addButton.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("New site node model"), {
      target: { value: "nodalarc:nodes/ground/selected.yaml" },
    });
    expect(addButton.disabled).toBe(false);
    fireEvent.click(addButton);

    await waitFor(() => expect(addCatalogDraftSiteNode).toHaveBeenCalledTimes(1));
    expect(addCatalogDraftSiteNode.mock.calls[0]![0]).toMatchObject({
      expected_draft_revision: 0,
      node_id: "edge-router",
      node_ref: "nodalarc:nodes/ground/selected.yaml",
    });
    expect(addCatalogDraftSiteNode.mock.calls[0]![0]).not.toHaveProperty("terminals");
    await waitFor(() => expect(screen.getByText("edge-router")).toBeTruthy());
    expect((screen.getByLabelText("access") as HTMLInputElement).value).toBe("2");
  });

  it("does not interpret inline objects as site-set members", () => {
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("site-sets", {
          sites: [{ site: { id: "legacy-inline", display_name: "Legacy inline site" } }],
        })}
        metadata={metadata("site-sets")}
        onSaved={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("invalid site reference 1")).toBeTruthy();
    expect(screen.queryByText("Legacy inline site")).toBeNull();
  });

  it("patches only the edited terminal field and preserves every advanced source field", async () => {
    const initial = draft("terminals", {
      medium: "rf",
      signal: { band: "ka", frequency_hz: 30e9 },
      bandwidth_mbps: { transmit: 50, receive: 60 },
      tracking_capacity: 1,
      max_range_km: 2500,
      limits: {
        elevation_deg: { min: 20, max: 90 },
        max_tracking_rate_deg_s: 2,
        vendor_limit: { mode: "keep-me" },
      },
      vendor_extension: { calibration: [1, 2, 3] },
    });
    const onSaved = vi.fn();
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("terminals")}
        onSaved={onSaved}
        onClose={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("name"), { target: { value: "Edited terminal" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));

    const request = patchCatalogDraft.mock.calls[0]![0] as CatalogDraftPatchRequest;
    expect(request.commands).toEqual([
      { operation: "replace", pointer: "/terminal/display_name", value: "Edited terminal" },
    ]);
    expect(request.draft.document).toEqual(initial.document);
    const saved = onSaved.mock.calls[0]![0] as CatalogDraftSaveResult;
    expect(saved.draft.document.terminal).toMatchObject({
      vendor_extension: { calibration: [1, 2, 3] },
      limits: { vendor_limit: { mode: "keep-me" } },
    });
  });

  it("carries the saved user revision fence into a second edit and save", async () => {
    const initial = draft("terminals", {}, { expectedTargetRevision: "revision-1" });
    saveCatalogDraft
      .mockImplementationOnce(async ({ draft: value }) => saveResult(value, "revision-2"))
      .mockImplementationOnce(async ({ draft: value }) => saveResult(value, "revision-3"));
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(
      (screen.getByRole("button", { name: "Save to library" }) as HTMLButtonElement).disabled,
    ).toBe(false));
    fireEvent.change(screen.getByLabelText("name"), { target: { value: "First edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(saveCatalogDraft).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("name"), { target: { value: "Second edit" } });
    await waitFor(() => expect(
      (screen.getByRole("button", { name: "Save to library" }) as HTMLButtonElement).disabled,
    ).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(saveCatalogDraft).toHaveBeenCalledTimes(2));

    expect((patchCatalogDraft.mock.calls[1]![0] as CatalogDraftPatchRequest).draft)
      .toMatchObject({ expected_target_revision: "revision-2" });
    expect(saveCatalogDraft.mock.calls[1]![0].draft)
      .toMatchObject({ expected_target_revision: "revision-2" });
  });

  it("surfaces affected sessions and components before a user overwrite", async () => {
    getCatalogDependents.mockResolvedValueOnce({
      target_ref: "user:nodes/test-node.yaml",
      target_revision: "revision-1",
      direct_dependents: [{
        ref: "user:constellations/dependent.yaml",
        family: "constellations",
        revision: "revision-dependent",
        minimum_depth: 1,
      }],
      transitive_dependents: [{
        ref: "user:sessions/experiment.yaml",
        family: "sessions",
        revision: "revision-session",
        minimum_depth: 2,
      }],
      overwrite_affects_dependents: true,
      delete_allowed: false,
      acknowledgement: "impact-1",
    });
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("nodes", {}, { expectedTargetRevision: "revision-1" })}
        metadata={metadata("nodes")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    expect(await screen.findByText(/user:constellations\/dependent.yaml/)).toBeTruthy();
    expect(screen.getByText(/user:sessions\/experiment.yaml/)).toBeTruthy();
  });

  it("renders structural and reference blockers with pointers and never calls save", async () => {
    const structuralIssue = {
      code: "catalog_draft.structural.missing",
      stage: "structural" as const,
      message: "Field required",
      pointer: "/orbit/shape",
      blocks: ["save" as const, "deploy" as const],
    };
    const initial = draft("orbits", {}, { issues: [structuralIssue] });
    compileCatalogDraft.mockImplementation(async ({ draft: value }) => ({
      draft: value,
      save_allowed: false,
      runtime_supported: false,
      canonical_yaml: null,
      canonical_json: null,
      content_digest: null,
      issues: [{
        code: "catalog_draft.reference.dangling",
        stage: "reference",
        message: "Referenced body does not exist",
        pointer: "/orbit/central_body",
        blocks: ["save", "deploy"],
      }],
    }));
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("orbits")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText(/Field required/).textContent).toContain("/orbit/shape");
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    expect(await screen.findByText(/Referenced body does not exist/)).toBeTruthy();
    expect(screen.getByText(/Referenced body does not exist/).textContent)
      .toContain("/orbit/central_body");
    expect(saveCatalogDraft).not.toHaveBeenCalled();
  });

  it.each(Object.keys(WRAPPERS) as CatalogComponentFamily[])(
    "opens, edits, compiles, and saves the full %s document",
    async (family) => {
      const onSaved = vi.fn();
      render(
        <CatalogDraftEditorWindow
          initialDraft={draft(family, { reference: "original" })}
          metadata={metadata(family)}
          onSaved={onSaved}
          onClose={() => {}}
        />,
      );
      if (["terminals", "nodes", "sites", "site-sets"].includes(family)) {
        fireEvent.click(screen.getByRole("button", { name: "Advanced JSON" }));
      }
      const textarea = await advancedTextarea();
      const object = JSON.parse(textarea.value) as Record<string, unknown>;
      object.reference = `edited-${family}`;
      fireEvent.change(textarea, { target: { value: JSON.stringify(object, null, 2) } });
      fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
      await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
      expect(compileCatalogDraft).toHaveBeenCalledTimes(1);
      expect(saveCatalogDraft).toHaveBeenCalledTimes(1);
      expect(replaceCatalogDraftObject).toHaveBeenCalledTimes(1);
    },
  );

  it("sends one revision-fenced raw object command for advanced edits", async () => {
    const original = Object.fromEntries(
      Array.from({ length: 70 }, (_, index) => [`field_${index}`, `original-${index}`]),
    );
    const initial = draft("payloads", original);
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("payloads")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    const textarea = await advancedTextarea();
    const object = JSON.parse(textarea.value) as Record<string, unknown>;
    for (let index = 0; index < 70; index += 1) object[`field_${index}`] = `edited-${index}`;
    fireEvent.change(textarea, { target: { value: JSON.stringify(object) } });
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(saveCatalogDraft).toHaveBeenCalledTimes(1));

    expect(patchCatalogDraft).not.toHaveBeenCalled();
    expect(replaceCatalogDraftObject).toHaveBeenCalledTimes(1);
    const request = replaceCatalogDraftObject.mock.calls[0]![0] as CatalogDraftReplaceObjectRequest;
    expect(request.expected_draft_revision).toBe(initial.draft_revision);
    expect(JSON.parse(request.raw_object_json)).toMatchObject({
      field_0: "edited-0",
      field_69: "edited-69",
    });
  });

  it("preserves dirty working state and revision fences for close and reload recovery", async () => {
    const initial = draft("terminals", {}, {
      revision: 7,
      expectedTargetRevision: "revision-target-7",
    });
    const onRecoveryChange = vi.fn();
    const onClose = vi.fn();
    const first = render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("terminals")}
        onSaved={() => {}}
        onClose={onClose}
        onRecoveryChange={onRecoveryChange}
      />,
    );
    fireEvent.change(screen.getByLabelText("name"), { target: { value: "Recovered edit" } });
    await waitFor(() => expect(onRecoveryChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        draft: expect.objectContaining({
          draft_revision: 7,
          expected_target_revision: "revision-target-7",
        }),
        workingDocument: expect.objectContaining({
          terminal: expect.objectContaining({ display_name: "Recovered edit" }),
        }),
      }),
    ));
    const recovery = (
      onRecoveryChange.mock.calls[onRecoveryChange.mock.calls.length - 1]![0]
    ) as CatalogDraftEditorRecovery;
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledWith(true);
    first.unmount();

    render(
      <CatalogDraftEditorWindow
        initialDraft={recovery.draft}
        initialRecovery={recovery}
        metadata={metadata("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    expect((screen.getByLabelText("name") as HTMLInputElement).value).toBe("Recovered edit");
    await waitFor(() => expect(
      (screen.getByRole("button", { name: "Save to library" }) as HTMLButtonElement).disabled,
    ).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(saveCatalogDraft).toHaveBeenCalledTimes(1));
    expect((patchCatalogDraft.mock.calls[0]![0] as CatalogDraftPatchRequest).draft)
      .toMatchObject({ draft_revision: 7, expected_target_revision: "revision-target-7" });
  });

  it("surfaces backend advanced JSON refusal without parsing the object locally", async () => {
    replaceCatalogDraftObject.mockRejectedValueOnce(new Error("Advanced component JSON is invalid"));
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("payloads")}
        metadata={metadata("payloads")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    const textarea = await advancedTextarea();
    fireEvent.change(textarea, { target: { value: "not-json" } });
    fireEvent.click(screen.getByRole("button", { name: "Validate edits" }));

    expect(await screen.findByText("Advanced component JSON is invalid")).toBeTruthy();
    expect(compileCatalogDraft).not.toHaveBeenCalled();
  });

  it("restores an unfinished advanced raw buffer without canonicalizing it away", async () => {
    const initial = draft("payloads", { reference: "baseline" }, { revision: 4 });
    const recovery: CatalogDraftEditorRecovery = {
      draft: initial,
      workingDocument: initial.document,
      advanced: true,
      advancedText: '{"id":"test-payload","reference":"unfinished',
    };
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        initialRecovery={recovery}
        metadata={metadata("payloads")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    expect((await advancedTextarea()).value).toBe(recovery.advancedText);
  });

  it("builds leaf JSON-pointer commands without replacing untouched siblings", () => {
    expect(catalogDraftFieldCommands(
      { terminal: { id: "x", limits: { elevation_deg: { min: 10, max: 90 }, vendor: true } } },
      { terminal: { id: "x", limits: { elevation_deg: { min: 20, max: 90 }, vendor: true } } },
      "terminal",
    )).toEqual([
      { operation: "replace", pointer: "/terminal/limits/elevation_deg/min", value: 20 },
    ]);
  });
});
