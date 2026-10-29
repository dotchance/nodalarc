// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Editors show an incomplete draft as incomplete: no null, zero or invented value. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { NodeEditor } = await import("../NodeEditor");
const { SessionEditor } = await import("../SessionEditor");
const { resetCatalogStores } = await import("../useBuilderWorld");
const { defaultDraftNode, newWorkspace } = await import("./fixtures/workspaceFixtures");

/** The editors list catalog objects on mount; these tests read only the draft they are given. */
function stubFetch() {
  globalThis.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;
}

beforeEach(() => resetCatalogStores());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("truthful incomplete node summaries", () => {
  it("renders incomplete node terminal facts without false values", () => {
    stubFetch();
    const node = defaultDraftNode();
    node.terminals = [
      {
        mount_id: "incomplete",
        role: null,
        terminal_ref: null,
        count: null,
        boresight: null,
      },
    ];
    render(
      <NodeEditor
        draft={node}
        onChange={() => {}}
        onAddTerminal={() => {}}
        onSetTerminalRole={() => {}}
        onAddEthernet={() => {}}
        authoring={AUTHORING_FACTS}
      />,
    );

    expect(document.body.textContent).toContain("role incomplete · count incomplete");
    expect(document.body.textContent).not.toContain("×null");
  });
});

describe("truthful incomplete session summaries", () => {
  it("never renders null time values as an authored rate", () => {
    const workspace = newWorkspace("incomplete-time");
    workspace.step_seconds = null;
    workspace.compression = null;
    render(<SessionEditor workspace={workspace} onUpdate={() => {}} />);

    expect(screen.getByText("time rate incomplete")).toBeTruthy();
    expect(document.body.textContent).not.toContain("×null");
    expect(document.body.textContent).not.toContain("step null");
  });

  it("labels a partially missing candidate budget as incomplete", () => {
    const workspace = newWorkspace("incomplete-budget");
    workspace.max_pairs_per_rule = null;
    render(<SessionEditor workspace={workspace} onUpdate={() => {}} />);

    expect(screen.getByText("candidate budget incomplete")).toBeTruthy();
    expect(document.body.textContent).not.toContain("null / rule");
  });
});
