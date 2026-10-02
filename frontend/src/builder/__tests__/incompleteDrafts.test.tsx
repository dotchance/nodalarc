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
const { ConstellationEditor } = await import("../ConstellationEditor");
const { SessionEditor } = await import("../SessionEditor");
const { RoutingDomainEditor } = await import("../RoutingEditor");
const { SegmentLinksCard } = await import("../SegmentLinksCard");
const { resetCatalogStores } = await import("../useBuilderWorld");
const {
  newDraftConstellation,
  defaultDraftNode,
  defaultRoutingDomain,
  newWorkspace,
} = await import("./fixtures/workspaceFixtures");

const NODE_REF = "user:nodes/gw.yaml";

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

  it("ConstellationEditor does not total incomplete terminal counts as zero", () => {
    stubFetch();
    const draft = newDraftConstellation(NODE_REF);
    draft.node_draft = defaultDraftNode();
    draft.node_draft.terminals = [
      {
        mount_id: "incomplete",
        role: "access",
        terminal_ref: null,
        count: null,
        boresight: null,
      },
    ];

    render(
      <ConstellationEditor
        authoring={AUTHORING_FACTS}
        draft={draft}
        onUpdate={() => {}}
        onUpdateOrbit={() => {}}
        onSetPopulation={async () => {}}
        onAuthorInlineNode={async () => {}}
        onAddNodeTerminal={async () => {}}
        onSetNodeTerminalRole={async () => {}}
        onAddNodeEthernet={async () => {}}
        onRemove={() => {}}
        workspace={newWorkspace("incomplete-terminal-count")}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );

    expect(document.body.textContent).toContain("terminal count incomplete");
    expect(document.body.textContent).not.toContain("· 0 terminals");
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

describe("truthful incomplete routing and link summaries", () => {
  it("labels partial routing timers without rendering null seconds", () => {
    const workspace = newWorkspace("partial-timers");
    const domain = defaultRoutingDomain(workspace);
    domain.hello_interval_s = 5;
    domain.hold_interval_s = null;
    workspace.routing_domains = [domain];

    render(
      <RoutingDomainEditor
        workspace={workspace}
        domain={domain}
        onUpdate={() => {}}
        onRemove={() => {}}
        authoring={AUTHORING_FACTS}
      />,
    );

    expect(screen.getByText("timers incomplete")).toBeTruthy();
    expect(document.body.textContent).not.toContain("nulls");
  });

  it("labels an incomplete endpoint role in a segment link summary", () => {
    const workspace = newWorkspace("partial-link");
    workspace.space_refs = [
      {
        segment_id: "space-1",
        source_ref: "nodalarc:constellations/a.yaml",
        label: "A",
      },
      {
        segment_id: "space-2",
        source_ref: "nodalarc:constellations/b.yaml",
        label: "B",
      },
    ];
    workspace.links = [
      {
        rule_id: "link-1",
        label: "A to B",
        enabled: true,
        a: {
          segment_id: "space-1",
          tag: null,
          role: null,
          medium: null,
          min_elevation_deg: null,
        },
        b: {
          segment_id: "space-2",
          tag: null,
          role: "isl",
          medium: "optical",
          min_elevation_deg: null,
        },
        topology_mode: null,
        topology_n: null,
        max_range_km: null,
      },
    ];

    render(
      <SegmentLinksCard
        workspace={workspace}
        segmentId="space-1"
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );

    expect(screen.getByText(/role incomplete · B/)).toBeTruthy();
  });
});
