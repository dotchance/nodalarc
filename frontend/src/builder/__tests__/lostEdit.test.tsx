// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Async editor writes either delegate semantic mutations to revision-fenced
 *  backend commands or read the latest local draft through functional updates.
 *
 *  The editor write contracts are functional-only — `onUpdate((prev) => next)` /
 *  `onChange((prev) => next)`. These pins TRIGGER each async writer, CAPTURE the
 *  functional updater it emits, then APPLY that updater to a state carrying a
 *  concurrent edit. A prev-based updater preserves the concurrent edit; a
 *  stale-closure writer (or a wrong `() => …` replacement) would drop it. This
 *  is deterministic — it proves the contract without racing real timers.
 *
 *  Ground/Site model changes are command delegation only. Node/Terminal edits
 *  remain local React state and retain the lost-edit functional-update pins.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import type { DraftSiteObject } from "../workspace";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { SiteEditor } = await import("../SiteEditor");
const { GroundEditor } = await import("../GroundEditor");
const { NodeEditor } = await import("../NodeEditor");
const { ConstellationEditor } = await import("../ConstellationEditor");
const { resetCatalogStores } = await import("../useBuilderWorld");
const {
  newDraftGroundSet,
  newDraftConstellation,
  defaultDraftNode,
  newWorkspace,
  testGroundMember,
} = await import("./fixtures/workspaceFixtures");

const NODE_REF = "user:nodes/gw.yaml";
const jsonOk = (body: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(body) });

/** Route the catalog-list endpoints used by editor selection controls. */
function stubFetch(opts?: { nodes?: unknown[]; terminals?: unknown[] }) {
  globalThis.fetch = vi.fn((url: string, init?: { body?: string }) => {
    const u = String(url);
    if (u.includes("/builder/catalog/list")) {
      const family = init?.body ? JSON.parse(init.body).family : null;
      const source = family === "nodes" ? opts?.nodes ?? [] : family === "terminals" ? opts?.terminals ?? [] : [];
      return Promise.resolve(
        jsonOk({
          generation: "g1",
          items: source.map((entry) => {
            const item = entry as {
              ref: string;
              family: string;
              display_name?: string;
              summary?: string | null;
            };
            return {
              ref: item.ref,
              namespace: item.ref.startsWith("user:") ? "user" : "nodalarc",
              family: item.family,
              revision: `revision-${item.ref}`,
              size_bytes: 100,
              display_name:
                item.display_name ?? (item.ref.split("/").pop() ?? item.ref).replace(/\.ya?ml$/, ""),
              summary: item.summary ?? null,
            };
          }),
          next_page_token: null,
        }),
      );
    }
    return Promise.resolve(jsonOk({}));
  }) as unknown as typeof fetch;
}

function draftSite(): DraftSiteObject {
  return {
    site_id: "my-site",
    display_name: "My site",
    body: "nodalarc:bodies/earth.yaml",
    lat_deg: 0,
    lon_deg: 0,
    alt_m: 0,
    lan_ipv4: "172.20.0.0/24",
    tags: [],
    nodes: [{
      node_id: "gw1",
      model_ref: NODE_REF,
      installed: {},
      boresights: {},
      lo0_ipv4: "10.200.0.1/32",
      terr0_ipv4: "172.20.0.1/24",
    }],
  };
}

beforeEach(() => resetCatalogStores());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
describe("backend-owned ground installation commands", () => {
  it("SiteEditor delegates a node model selection without parsing catalog JSON", async () => {
    stubFetch({ nodes: [{ ref: NODE_REF, family: "nodes", display_name: "GW" }] });
    const site = draftSite();
    const onSetNodeModel = vi.fn(async () => {});
    const onUpdate = vi.fn();
    render(
      <SiteEditor
        site={site}
        onUpdate={onUpdate}
        onSetNodeModel={onSetNodeModel}
        onAddNode={async () => {}}
        authoring={AUTHORING_FACTS}
      />,
    );
    await screen.findByRole("option", { name: /gw/i });
    fireEvent.change(screen.getByLabelText("gw1 model"), { target: { value: NODE_REF } });
    await waitFor(() => expect(onSetNodeModel).toHaveBeenCalledWith("gw1", NODE_REF));
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("GroundEditor delegates stamp and nested site model selections", async () => {
    stubFetch({ nodes: [{ ref: NODE_REF, family: "nodes", display_name: "GW" }] });
    const ground = newDraftGroundSet(NODE_REF, {});
    ground.members = [testGroundMember(ground, "Denver", 39.7, -104.9)];
    const member = ground.members[0]!;
    const onSetStampNodeModel = vi.fn(async () => {});
    const onSetSiteNodeModel = vi.fn(async () => {});
    const onUpdate = vi.fn();
    render(
      <GroundEditor
        authoring={AUTHORING_FACTS}
        draft={ground}
        onUpdate={onUpdate}
        onMintSites={async () => {}}
        onSetStampNodeModel={onSetStampNodeModel}
        onSetSiteNodeModel={onSetSiteNodeModel}
        onAddSiteNode={async () => {}}
        onRemove={() => {}}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
        schedulingPresets={[]}
        selectedSchedulingPreset={null}
        memberSchedulingPreset={() => null}
        onSchedulingPreset={async () => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /New-site stamp/ }));
    await screen.findByRole("option", { name: /gw/i });
    fireEvent.change(screen.getByLabelText("Stamp node model"), { target: { value: NODE_REF } });
    await waitFor(() => expect(onSetStampNodeModel).toHaveBeenCalledWith(NODE_REF));

    fireEvent.click(screen.getByRole("button", { name: /Sites/ }));
    fireEvent.click(screen.getByRole("button", { name: `Edit ${member.label}` }));
    fireEvent.change(screen.getByLabelText("gw1 model"), { target: { value: NODE_REF } });
    await waitFor(() =>
      expect(onSetSiteNodeModel).toHaveBeenCalledWith(member.member_id, "gw1", NODE_REF),
    );
    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("backend-owned node creation intents", () => {
  it("NodeEditor sends only terminal and role intent", async () => {
    stubFetch({
      terminals: [{ ref: "user:terminals/ka.yaml", family: "terminals", id: "ka", display_name: "Ka" }],
    });
    const n0 = defaultDraftNode();
    const onChange = vi.fn();
    const onAddTerminal = vi.fn();
    const onAddEthernet = vi.fn();
    render(
      <NodeEditor
        draft={n0}
        onChange={onChange}
        onAddTerminal={onAddTerminal}
        onAddEthernet={onAddEthernet}
        authoring={AUTHORING_FACTS}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "+ port" }));
    const option = await screen.findByText(/ka/i);
    fireEvent.click(option.closest("button")!);
    await waitFor(() =>
      expect(onAddTerminal).toHaveBeenCalledWith("user:terminals/ka.yaml", "access"),
    );
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "+ lan" }));
    expect(onAddEthernet).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ConstellationEditor forwards node terminal intent without creating a mount", async () => {
    stubFetch({
      terminals: [{ ref: "user:terminals/ka.yaml", family: "terminals", id: "ka", display_name: "Ka" }],
    });
    const c0 = newDraftConstellation(NODE_REF);
    c0.node_draft = defaultDraftNode();
    const onUpdate = vi.fn();
    const onAddNodeTerminal = vi.fn(async () => {});
    render(
      <ConstellationEditor
        authoring={AUTHORING_FACTS}
        draft={c0}
        onUpdate={onUpdate}
        onUpdateOrbit={() => {}}
        onSetPopulation={async () => {}}
        onAuthorInlineNode={async () => {}}
        onAddNodeTerminal={onAddNodeTerminal}
        onAddNodeEthernet={async () => {}}
        onRemove={() => {}}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Node/ }));
    fireEvent.click(screen.getByRole("button", { name: "+ port" }));
    const option = await screen.findByText(/ka/i);
    fireEvent.click(option.closest("button")!);
    await waitFor(() =>
      expect(onAddNodeTerminal).toHaveBeenCalledWith("user:terminals/ka.yaml", "access"),
    );
    expect(onUpdate).not.toHaveBeenCalled();
  });
});
