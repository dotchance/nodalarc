import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  CatalogComponentDraftEnvelope,
  CatalogComponentFamily,
  JsonValue,
} from "../generated/builderApi";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";

// These tests read only what the editor draws from the draft it is given. No VS-API call
// ever answers, so nothing a test asserts comes from a scripted response.
const unanswered = vi.hoisted(() => () => new Promise<never>(() => {}));

vi.mock("../builderApiClient", () => ({
  addCatalogDraftNodeEthernet: unanswered,
  addCatalogDraftNodeTerminal: unanswered,
  addCatalogDraftSiteNode: unanswered,
  applyCatalogDraftYaml: unanswered,
  compileCatalogDraft: unanswered,
  saveCatalogDraft: unanswered,
  getCatalogDependents: unanswered,
  mutateCatalogDraftControls: unanswered,
}));

vi.mock("../useBuilderWorld", () => ({
  useBuilderCatalog: () => ({ entries: [], error: null, refresh: () => Promise.resolve() }),
}));

const { CatalogDraftEditorWindow: CatalogDraftEditorWindowBase } = await import(
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
    state: {
      draft_revision: options.revision ?? 0,
      family,
      target_ref: `user:${family}/${objectId}.yaml`,
      source_ref: `nodalarc:${family}/source.yaml`,
      expected_source_revision: "revision-source",
      expected_target_revision: options.expectedTargetRevision ?? null,
      document,
    },
    projected_yaml: projectedYaml(document),
    control_tree: emptyControlTree(options.revision ?? 0),
    issues: options.issues ?? [],
  };
}

afterEach(cleanup);

describe("CatalogDraftEditorWindow", () => {
  it("offers only the gestures whose shape the backend derives, beside the field editor", () => {
    // Every component field is edited in the backend's control tree; the window
    // adds a node's terminal mounts and LAN ports and a site's installed nodes,
    // which the backend shapes from the referenced object.
    const { unmount } = render(
      <CatalogDraftEditorWindow
        initialDraft={draft("nodes")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("+ terminal mount")).toBeTruthy();
    expect(screen.getByText("+ LAN port")).toBeTruthy();
    expect(screen.queryByText("+ add node")).toBeNull();
    unmount();

    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("sites")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("+ add node")).toBeTruthy();
    expect(screen.queryByText("+ terminal mount")).toBeNull();
  });

  it("tells its host when it holds edits not yet saved", () => {
    // The host asks before another component or a close discards these edits.
    const dirty: boolean[] = [];
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
        onDirtyChange={(value) => dirty.push(value)}
      />,
    );
    expect(dirty[dirty.length - 1]).toBe(false);
    fireEvent.change(screen.getByLabelText("Component YAML"), {
      target: { value: "terminal:\n  id: test-terminal\n  display_name: edited\n" },
    });
    expect(dirty[dirty.length - 1]).toBe(true);
  });

  it("offers no gesture for a component with no derived shape", () => {
    render(
      <CatalogDraftEditorWindow
        initialDraft={draft("terminals")}
        onSaved={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByTestId("catalog-graphical-editor")).toBeTruthy();
    expect(screen.queryByText("+ terminal mount")).toBeNull();
    expect(screen.queryByText("+ add node")).toBeNull();
  });
});
