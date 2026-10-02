import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  CatalogComponentDraftEnvelope,
  CatalogComponentFamily,
  CatalogFamilyMetadata,
  JsonValue,
} from "../generated/builderApi";
import type { CatalogDraftEditorRecovery } from "../structuredDraftRecovery";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";

// These tests read only what the editor draws from the draft it is given. No VS-API call
// ever answers, so nothing a test asserts comes from a scripted response.
const unanswered = vi.hoisted(() => () => new Promise<never>(() => {}));

vi.mock("../builderApiClient", () => ({
  addCatalogDraftNodeEthernet: unanswered,
  addCatalogDraftNodeTerminal: unanswered,
  addCatalogDraftSiteNode: unanswered,
  applyCatalogDraftYaml: unanswered,
  patchCatalogDraft: unanswered,
  compileCatalogDraft: unanswered,
  saveCatalogDraft: unanswered,
  getCatalogDependents: unanswered,
  mutateCatalogDraftControls: unanswered,
}));

vi.mock("../useBuilderWorld", () => ({
  useBuilderCatalog: () => ({ entries: [], error: null, refresh: () => Promise.resolve() }),
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

async function yamlTextarea(): Promise<HTMLTextAreaElement> {
  const textarea = screen.getByLabelText("Component YAML") as HTMLTextAreaElement;
  await waitFor(() => expect(textarea.value).toContain("id:"));
  return textarea;
}

afterEach(cleanup);

describe("CatalogDraftEditorWindow", () => {
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
