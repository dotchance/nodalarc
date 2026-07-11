import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CatalogComponentDraftEnvelope,
  CatalogComponentFamily,
  CatalogDraftAddNodeEthernetPortRequest,
  CatalogDraftAddNodeTerminalMountRequest,
  CatalogDraftAddSiteNodeRequest,
  CatalogDraftApplyYamlRequest,
  CatalogDraftApplyYamlResult,
  CatalogDraftPatchRequest,
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
  applyCatalogDraftYaml: vi.fn(),
  patchCatalogDraft: vi.fn(),
  compileCatalogDraft: vi.fn(),
  saveCatalogDraft: vi.fn(),
  getCatalogDependents: vi.fn(),
  mutateCatalogDraftControls: vi.fn(),
}));
const {
  addCatalogDraftNodeEthernet,
  addCatalogDraftNodeTerminal,
  addCatalogDraftSiteNode,
  applyCatalogDraftYaml,
  patchCatalogDraft,
  compileCatalogDraft,
  saveCatalogDraft,
  getCatalogDependents,
  mutateCatalogDraftControls,
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

function projectedYaml(document: Readonly<Record<string, JsonValue>>): string {
  const [wrapper, value] = Object.entries(document)[0] ?? ["component", {}];
  const object = value && typeof value === "object" && !Array.isArray(value)
    ? value as Readonly<Record<string, JsonValue>>
    : {};
  const lines = [`${wrapper}:`];
  for (const [key, child] of Object.entries(object)) {
    if (typeof child === "string" || typeof child === "number" || typeof child === "boolean") {
      lines.push(`  ${key}: ${String(child)}`);
    } else if (Array.isArray(child)) {
      lines.push(`  ${key}: []`);
    } else {
      lines.push(`  ${key}: {}`);
    }
  }
  return `${lines.join("\n")}\n`;
}

function emptyControlTree(revision: number): CatalogComponentDraftEnvelope["control_tree"] {
  return {
    projection_revision: revision,
    root: {
      control_id: "ctl_00000000000000000000000000000000",
      json_pointer: "",
      label: "Catalog component",
      required: true,
      present: true,
      model_name: "tests.CatalogComponent",
      fields: [],
    },
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
  const document = {
    [wrapper]: {
      id: objectId,
      display_name: `Test ${family}`,
      ...object,
    },
  };
  return {
    contract_version: 1,
    draft_revision: options.revision ?? 0,
    family,
    target_ref: `user:${family}/${objectId}.yaml`,
    source_ref: `nodalarc:${family}/source.yaml`,
    expected_source_revision: "revision-source",
    expected_target_revision: options.expectedTargetRevision ?? null,
    document,
    projected_yaml: projectedYaml(document),
    control_tree: emptyControlTree(options.revision ?? 0),
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
  const patchedDocument = document as Readonly<Record<string, JsonValue>>;
  return {
    ...request.draft,
    draft_revision: request.draft.draft_revision + 1,
    control_tree: emptyControlTree(request.draft.draft_revision + 1),
    document: patchedDocument,
    projected_yaml: projectedYaml(patchedDocument),
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
    control_tree: emptyControlTree(input.draft_revision + 1),
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
  mutateCatalogDraftControls.mockImplementation(async (request) => request.draft);
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
        projected_yaml: projectedYaml(document),
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
        projected_yaml: projectedYaml(document),
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
        projected_yaml: projectedYaml(document),
        issues: [],
      };
    },
  );
  patchCatalogDraft.mockImplementation(async (request: CatalogDraftPatchRequest) =>
    applyPatch(request),
  );
  applyCatalogDraftYaml.mockImplementation(
    async (request: CatalogDraftApplyYamlRequest): Promise<CatalogDraftApplyYamlResult> => {
      const updated = {
        ...request.draft,
        draft_revision: request.draft.draft_revision + 1,
        control_tree: emptyControlTree(request.draft.draft_revision + 1),
        projected_yaml: request.yaml_text,
        issues: [],
      };
      return {
        draft: updated,
        yaml_text: request.yaml_text,
        applied: true,
        canonicalization_required: false,
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

async function yamlTextarea(): Promise<HTMLTextAreaElement> {
  const textarea = screen.getByLabelText("Component YAML") as HTMLTextAreaElement;
  await waitFor(() => expect(textarea.value).toContain("id:"));
  return textarea;
}

beforeEach(() => {
  vi.clearAllMocks();
  installHappyPath();
});

afterEach(cleanup);

describe("CatalogDraftEditorWindow", () => {
  it("regenerates the YAML buffer from the backend after graphical edits", async () => {
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("terminals", { medium: "rf" })}
        metadata={metadata("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("name"), { target: { value: "Graphical edit" } });

    await waitFor(() => expect(patchCatalogDraft).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((screen.getByLabelText("Component YAML") as HTMLTextAreaElement).value)
      .toContain("display_name: Graphical edit"));
    expect(applyCatalogDraftYaml).not.toHaveBeenCalled();
  });

  it("debounces YAML through VS-API and ignores stale buffer generations", async () => {
    let resolveFirst: (result: CatalogDraftApplyYamlResult) => void = () => {};
    let resolveSecond: (result: CatalogDraftApplyYamlResult) => void = () => {};
    applyCatalogDraftYaml
      .mockImplementationOnce((_request: CatalogDraftApplyYamlRequest) => new Promise((resolve) => {
        resolveFirst = resolve;
      }))
      .mockImplementationOnce((_request: CatalogDraftApplyYamlRequest) => new Promise((resolve) => {
        resolveSecond = resolve;
      }));
    const initial = draft("terminals", { medium: "rf" });
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    const textarea = await yamlTextarea();
    const firstYaml = "terminal:\n  display_name: First\n  id: test-terminal\n  medium: rf\n";
    const secondYaml = "terminal:\n  display_name: Second\n  id: test-terminal\n  medium: rf\n";

    fireEvent.change(textarea, { target: { value: firstYaml } });
    await waitFor(() => expect(applyCatalogDraftYaml).toHaveBeenCalledTimes(1));
    fireEvent.change(textarea, { target: { value: secondYaml } });
    await act(async () => {
      resolveFirst({
        draft: {
          ...initial,
          draft_revision: 1,
          document: { terminal: { id: "test-terminal", display_name: "First", medium: "rf" } },
          projected_yaml: firstYaml,
        },
        yaml_text: firstYaml,
        applied: true,
        canonicalization_required: false,
        issues: [],
      });
    });
    await waitFor(() => expect(applyCatalogDraftYaml).toHaveBeenCalledTimes(2));
    expect((screen.getByLabelText("name") as HTMLInputElement).value).not.toBe("First");
    await act(async () => {
      resolveSecond({
        draft: {
          ...initial,
          draft_revision: 1,
          document: { terminal: { id: "test-terminal", display_name: "Second", medium: "rf" } },
          projected_yaml: secondYaml,
        },
        yaml_text: secondYaml,
        applied: true,
        canonicalization_required: false,
        issues: [],
      });
    });

    await waitFor(() => expect((screen.getByLabelText("name") as HTMLInputElement).value)
      .toBe("Second"));
    expect(textarea.value).toBe(secondYaml);
  });

  it("leaves missing catalog numbers empty and preserves nullable frequency conversion", () => {
    const first = render(
      <CatalogDraftEditorWindow
        initialDraft={draft("terminals", {
          medium: "rf",
          signal: { band: "ka" },
          bandwidth_mbps: {},
          limits: { elevation_deg: {} },
        })}
        metadata={metadata("terminals")}
        onSaved={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    for (const label of [
      "frequency",
      "tx bandwidth",
      "rx bandwidth",
      "tracking capacity",
      "max range",
      "min elevation",
      "max elevation",
      "max tracking rate",
    ]) {
      expect(
        (screen.getByLabelText(new RegExp(`^${label}`)) as HTMLInputElement).value,
      ).toBe("");
    }
    first.unmount();

    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("sites", {
          frame: { body_fixed: { body: "nodalarc:bodies/earth.yaml" } },
          location: {},
          lan: { ipv4: "" },
          nodes: [],
        })}
        metadata={metadata("sites")}
        onSaved={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    for (const label of ["latitude", "longitude", "altitude"]) {
      expect(
        (screen.getByLabelText(new RegExp(`^${label}`)) as HTMLInputElement).value,
      ).toBe("");
    }
  });

  it("shows missing mount and installed-node identities without inventing ids", () => {
    const first = render(
      <CatalogDraftEditorWindow
        initialDraft={draft("nodes", {
          forwarding: "routed",
          ethernet: [],
          terminals: [
            {
              role: "access",
              terminal: "nodalarc:terminals/rf/selected.yaml",
            },
          ],
        })}
        metadata={metadata("nodes")}
        onSaved={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("mount id incomplete")).toBeTruthy();
    expect((screen.getByLabelText("count") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("mount-1");
    first.unmount();

    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("sites", {
          frame: { body_fixed: { body: "nodalarc:bodies/earth.yaml" } },
          location: {},
          lan: { ipv4: "" },
          nodes: [
            {
              model: "nodalarc:nodes/ground/selected.yaml",
              terminals: { access: {} },
              interfaces: { lo0: { ipv4: "" }, terr0: { ipv4: "" } },
            },
          ],
        })}
        metadata={metadata("sites")}
        onSaved={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("node id incomplete")).toBeTruthy();
    expect((screen.getByLabelText("access") as HTMLInputElement).value).toBe("");
    expect(document.body.textContent).not.toContain("node-1");
  });

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

  it("does not overlay body-fixed fields onto a non-body-fixed site frame", () => {
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("sites", {
          frame: {
            lagrange: {
              primary: "nodalarc:bodies/earth.yaml",
              secondary: "nodalarc:bodies/luna.yaml",
              point: "L1",
            },
          },
          lan: { ipv4: "10.0.0.0/24" },
          nodes: [],
        })}
        metadata={metadata("sites")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.queryByLabelText("Site body")).toBeNull();
    expect(screen.getByText(
      "This site uses a non-body-fixed frame. Its frame fields are edited below.",
    )).toBeTruthy();
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

  it("does not replace a terminal signal when its active medium is reselected", async () => {
    const initial = draft("terminals", {
      medium: "rf",
      signal: { band: "custom-ka", frequency_hz: 31.25e9 },
    });
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "RF" }));

    await new Promise((resolve) => window.setTimeout(resolve, 350));
    expect(patchCatalogDraft).not.toHaveBeenCalled();
    expect((screen.getByLabelText("band") as HTMLInputElement).value).toBe("custom-ka");
  });

  it("preserves Ethernet port tags when the specialized form renames a port", async () => {
    const initial = draft("nodes", {
      forwarding: "routed",
      terminals: [],
      ethernet: [{ id: "terr0", tags: ["uplink", "preserve"] }],
      payloads: [],
    });
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("nodes")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText("LAN port"), { target: { value: "terr9" } });

    await waitFor(() => expect(patchCatalogDraft).toHaveBeenCalled());
    const request = patchCatalogDraft.mock.calls[0]![0] as CatalogDraftPatchRequest;
    expect(request.commands).toEqual([{
      operation: "replace",
      pointer: "/node/ethernet",
      value: [{ id: "terr9", tags: ["uplink", "preserve"] }],
    }]);
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
    "opens graphical controls with synchronized YAML and saves the full %s document",
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
      expect(screen.queryByText("Advanced JSON")).toBeNull();
      expect(screen.getByTestId("catalog-graphical-editor")).toBeTruthy();
      const textarea = await yamlTextarea();
      const editedYaml = `${WRAPPERS[family]}:\n  id: test-${family.replace(/s$/, "")}\n  reference: edited-${family}\n`;
      fireEvent.change(textarea, { target: { value: editedYaml } });
      fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
      await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
      expect(compileCatalogDraft).toHaveBeenCalledTimes(1);
      expect(saveCatalogDraft).toHaveBeenCalledTimes(1);
      expect(applyCatalogDraftYaml).toHaveBeenCalledTimes(1);
    },
  );

  it("sends one revision-fenced exact YAML buffer without parsing it in the browser", async () => {
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
    const textarea = await yamlTextarea();
    const yamlText = [
      "payload:",
      "  id: test-payload",
      ...Array.from({ length: 70 }, (_, index) => `  field_${index}: edited-${index}`),
      "",
    ].join("\n");
    fireEvent.change(textarea, { target: { value: yamlText } });
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(saveCatalogDraft).toHaveBeenCalledTimes(1));

    expect(patchCatalogDraft).not.toHaveBeenCalled();
    expect(applyCatalogDraftYaml).toHaveBeenCalledTimes(1);
    const request = applyCatalogDraftYaml.mock.calls[0]![0] as CatalogDraftApplyYamlRequest;
    expect(request.expected_draft_revision).toBe(initial.draft_revision);
    expect(request.yaml_text).toBe(yamlText);
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

  it("keeps the last graphical projection when backend YAML parsing is refused", async () => {
    const initial = draft("payloads", { reference: "baseline" });
    applyCatalogDraftYaml.mockImplementationOnce(async (request: CatalogDraftApplyYamlRequest) => ({
      draft: request.draft,
      yaml_text: request.yaml_text,
      applied: false,
      canonicalization_required: false,
      issues: [{
        code: "catalog_draft.yaml.invalid_syntax",
        stage: "structural",
        message: "Catalog component YAML is invalid",
        pointer: "/",
        source_line: 2,
        source_column: 7,
        blocks: ["save", "deploy"],
      }],
    }));
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("payloads")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    const textarea = await yamlTextarea();
    fireEvent.change(textarea, { target: { value: "payload:\n  id: [" } });
    fireEvent.click(screen.getByRole("button", { name: "Validate edits" }));

    expect(await screen.findByText(/Catalog component YAML is invalid/)).toBeTruthy();
    expect(screen.getByText(/line 2:7/)).toBeTruthy();
    expect(screen.getByTestId("catalog-yaml-stale-marker").textContent)
      .toContain("Showing applied revision 0");
    expect(textarea.value).toBe("payload:\n  id: [");
    expect(compileCatalogDraft).not.toHaveBeenCalled();
  });

  it("requires explicit acknowledgement before canonicalization can enable save", async () => {
    const initial = draft("terminals", { medium: "rf" });
    const canonicalYaml = "terminal:\n  display_name: Edited\n  id: test-terminal\n  medium: rf\n";
    applyCatalogDraftYaml.mockImplementationOnce(async (request: CatalogDraftApplyYamlRequest) => {
      const updated = {
        ...request.draft,
        draft_revision: request.draft.draft_revision + 1,
        document: {
          terminal: { id: "test-terminal", display_name: "Edited", medium: "rf" },
        },
        projected_yaml: canonicalYaml,
        issues: [],
      };
      return {
        draft: updated,
        yaml_text: request.yaml_text,
        applied: true,
        canonicalization_required: true,
        issues: [],
      };
    });
    render(
      <CatalogDraftEditorWindow
        initialDraft={initial}
        metadata={metadata("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    const textarea = await yamlTextarea();
    const exactYaml = `# operator note\n${canonicalYaml}`;
    fireEvent.change(textarea, { target: { value: exactYaml } });
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));

    expect(await screen.findByTestId("catalog-canonicalization-warning")).toBeTruthy();
    expect(textarea.value).toBe(exactYaml);
    expect(saveCatalogDraft).not.toHaveBeenCalled();
    expect((screen.getByLabelText("name").closest("fieldset") as HTMLFieldSetElement).disabled)
      .toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Use canonical YAML" }));
    expect(textarea.value).toBe(canonicalYaml);
    expect((screen.getByLabelText("name").closest("fieldset") as HTMLFieldSetElement).disabled)
      .toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(saveCatalogDraft).toHaveBeenCalledTimes(1));
  });

  it("restores an unfinished YAML buffer without canonicalizing it away", async () => {
    const initial = draft("payloads", { reference: "baseline" }, { revision: 4 });
    const recovery: CatalogDraftEditorRecovery = {
      draft: initial,
      baselineDocument: initial.document,
      workingDocument: initial.document,
      yamlText: "payload:\n  id: test-payload\n  reference: [unfinished",
      appliedYamlText: initial.projected_yaml,
      canonicalizationRequired: false,
      canonicalizationAccepted: false,
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

    expect((await yamlTextarea()).value).toBe(recovery.yamlText);
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
