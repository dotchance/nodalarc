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
const { RoutingDomainWindow } = await import("../RoutingEditor");
const { SegmentLinksCard } = await import("../SegmentLinksCard");
const { resetCatalogStores } = await import("../useBuilderWorld");
const { listSessionObjects } = await import("../sessionListing");
const { newDraftConstellation, defaultDraftNode, newWorkspace } = await import(
  "./fixtures/workspaceFixtures"
);
const {
  acceptedDomain,
  acceptedRule,
  domainAddress,
  domainCard,
  pendingAnswer,
  ruleAddress,
  ruleCard,
  sessionListing,
  workingIssue,
} = await import("./fixtures/listingFixtures");

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
        linkRules={[]}
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
  it("states the backend's issue for a domain whose pending edit set one timer", () => {
    const workspace = newWorkspace("partial-timers");
    workspace.space_refs = [
      { segment_id: "leo", source_ref: "nodalarc:constellations/leo.yaml", label: "LEO" },
    ];
    // The pending edit set hello to 5 and left hold unset. The backend writes
    // hello alone, states no card value and names the missing hold.
    const answer = pendingAnswer(domainAddress("leo-core"), null, [
      workingIssue(
        "Routing timers require both hello and hold intervals",
        "/timers/hold_interval_s",
      ),
    ]);
    const [domain] = listSessionObjects(
      sessionListing({
        routing_domains: [
          acceptedDomain("leo-core", "isis", ["leo"], domainCard("leo-core", "isis", ["leo"])),
        ],
      }),
      [
        {
          ...answer,
          control_tree: {
            projection_revision: 1,
            root: { ...answer.control_tree.root, model_name: "RoutingDomain", fields: [] },
          },
        },
      ],
    ).routingDomains;

    render(
      <RoutingDomainWindow
        listed={domain!}
        appliedRevision={1}
        onEdit={() => Promise.reject(new Error("nothing is edited"))}
        onDescribe={() => Promise.reject(new Error("the answer's field editor is current"))}
        workspace={workspace}
        onRemove={null}
        authoring={AUTHORING_FACTS}
      />,
    );

    expect(screen.getByText("Pending edit. It is not applied to the session yet.")).toBeTruthy();
    expect(screen.getByText("Routing timers require both hello and hold intervals")).toBeTruthy();
    expect(screen.getByText("/timers/hold_interval_s")).toBeTruthy();
    expect(screen.queryByTestId("builder-domain-editor")).toBeNull();
    expect(document.body.textContent).not.toContain("null");
  });

  it("states no role for a rule whose pending edit lost its endpoint role", () => {
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
    // The accepted rule is a crosslink. Its pending edit removed the first
    // endpoint's role, so the backend answers no card value and an issue.
    const listed = listSessionObjects(
      sessionListing({
        link_rules: [
          acceptedRule(
            "link-1",
            ["space-1", "space-2"],
            ruleCard("link-1", "space-1", "space-2", "crosslink"),
          ),
        ],
      }),
      [
        pendingAnswer(ruleAddress("link-1"), null, [
          workingIssue("Field required", "/endpoints/0/terminal/all/0/role"),
        ]),
      ],
    );

    render(
      <SegmentLinksCard
        workspace={workspace}
        rules={listed.linkRules}
        segmentId="space-1"
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );

    expect(screen.getByText("link-1")).toBeTruthy();
    expect(screen.getByText("pending edit")).toBeTruthy();
    expect(document.body.textContent).not.toContain("crosslink");
    expect(document.body.textContent).not.toContain("null");
  });
});
