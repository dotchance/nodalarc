// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** lost-edit class: async writers must read the LATEST draft, never a stale
 *  render-closure, so a concurrent edit made during an in-flight fetch survives.
 *
 *  The editor write contracts are functional-only — `onUpdate((prev) => next)` /
 *  `onChange((prev) => next)`. These pins TRIGGER each async writer, CAPTURE the
 *  functional updater it emits, then APPLY that updater to a state carrying a
 *  concurrent edit. A prev-based updater preserves the concurrent edit; a
 *  stale-closure writer (or a wrong `() => …` replacement) would drop it. This
 *  is deterministic — it proves the contract without racing real timers.
 *
 *  Two classes, both covered: Class A writes through the buffer (Ground/Site);
 *  Class B writes through local React state (Node/Terminal in the library /
 *  embedded contexts). Plus the reseed exception: seed-from-catalog REPLACES,
 *  stated as `() => replacement`, so it drops prev BY DESIGN.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";

vi.mock("../../config", () => ({
  REST_URL: "http://test:8080",
  authHeaders: (extra?: Record<string, string>) => ({ ...extra }),
}));

const { SiteEditor } = await import("../SiteEditor");
const { GroundEditor } = await import("../GroundEditor");
const { NodeEditor } = await import("../NodeEditor");
const { TerminalEditor } = await import("../TerminalEditor");
const { ConstellationEditor } = await import("../ConstellationEditor");
const { resetCatalogStores } = await import("../useBuilderWorld");
const {
  newDraftSiteObject,
  newDraftGroundSet,
  newDraftConstellation,
  defaultDraftNode,
  defaultDraftTerminal,
  mintSiteMembers,
  parseSiteLines,
  refGroundMember,
  siteObjectFromDraft,
  newWorkspace,
} = await import("../workspace");

const NODE_REF = "user:nodes/gw.yaml";
const jsonOk = (body: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(body) });

/** Route every endpoint the editors touch. `object` is the catalog-object
 *  document a fetch-based writer parses; the nodes/terminals family lists carry
 *  the ref a SelectField needs as an option; save echoes a ref. */
function stubFetch(opts?: { object?: unknown; nodes?: unknown[]; terminals?: unknown[] }) {
  globalThis.fetch = vi.fn((url: string, init?: { body?: string }) => {
    const u = String(url);
    if (u.includes("/builder/catalog/object")) return Promise.resolve(jsonOk(opts?.object ?? {}));
    if (u.includes("/builder/catalog/save")) {
      const family = init?.body ? JSON.parse(init.body).family : "x";
      return Promise.resolve(jsonOk({ ref: `user:${family}/saved.yaml`, family }));
    }
    if (u.includes("/builder/catalog")) {
      const family = new URL(u).searchParams.get("family");
      if (family === "nodes") return Promise.resolve(jsonOk(opts?.nodes ?? []));
      if (family === "terminals") return Promise.resolve(jsonOk(opts?.terminals ?? []));
      return Promise.resolve(jsonOk([]));
    }
    return Promise.resolve(jsonOk({}));
  }) as unknown as typeof fetch;
}

beforeEach(() => resetCatalogStores());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("lost-edit: async writers read the latest draft (class A — through the buffer)", () => {
  it("SiteEditor.setNodeModel preserves a concurrently-added node", async () => {
    stubFetch({
      object: { document: { node: { terminals: [{ id: "access", count: 2 }] } } },
      nodes: [{ ref: NODE_REF, family: "nodes", id: "gw", display_name: "GW" }],
    });
    const s0 = newDraftSiteObject(NODE_REF, {});
    const onUpdate = vi.fn();
    render(<SiteEditor site={s0} onUpdate={onUpdate} />);
    // Switch the first node's model — an async catalog fetch, then updateNode.
    await screen.findByRole("option", { name: "GW" }); // wait for the catalog to load the option
    fireEvent.change(screen.getByLabelText(`${s0.nodes[0]!.node_id} model`), {
      target: { value: NODE_REF },
    });
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const updater = onUpdate.mock.calls[0]![0] as (p: typeof s0) => typeof s0;

    // A second node was added DURING the fetch — it lives only in the latest state.
    const concurrent = {
      ...s0,
      nodes: [...s0.nodes, { ...s0.nodes[0]!, node_id: "gw2" }],
    };
    const result = updater(concurrent);
    expect(result.nodes[0]!.model_ref).toBe(NODE_REF); // the writer's own change lands
    expect(result.nodes.map((n) => n.node_id)).toEqual(["gw1", "gw2"]); // concurrent node survives
  });

  it("SiteEditor.setNodeModel targets the node by id when an EARLIER node is removed mid-fetch", async () => {
    // Concurrent-remove is the case an index-based write gets wrong: switching
    // gw2's model (async) while gw1 is deleted shifts gw2 to index 0, so an
    // index-1 write would miss it entirely. The stable-id match must still land.
    stubFetch({
      object: { document: { node: { terminals: [{ id: "access", count: 2 }] } } },
      nodes: [{ ref: NODE_REF, family: "nodes", id: "gw", display_name: "GW" }],
    });
    const base = newDraftSiteObject(NODE_REF, {});
    const mk = (id: string) => ({ ...base.nodes[0]!, node_id: id, model_ref: "user:nodes/old.yaml" });
    const s0 = { ...base, nodes: [mk("gw1"), mk("gw2")] };
    const onUpdate = vi.fn();
    render(<SiteEditor site={s0} onUpdate={onUpdate} />);
    await screen.findAllByRole("option", { name: "GW" });
    fireEvent.change(screen.getByLabelText("gw2 model"), { target: { value: NODE_REF } });
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const updater = onUpdate.mock.calls[0]![0] as (p: typeof s0) => typeof s0;

    // gw1 was removed DURING the fetch — only gw2 remains, now at index 0.
    const result = updater({ ...s0, nodes: [s0.nodes[1]!] });
    expect(result.nodes).toHaveLength(1);
    const gw2 = result.nodes.find((n) => n.node_id === "gw2")!;
    expect(gw2.model_ref).toBe(NODE_REF); // landed on gw2 by id, not the empty index-1 slot
    expect(gw2.installed.access).toBe(2); // re-seeded from the fetched faceplate
  });

  it("GroundEditor.setStampModel preserves a concurrent stamp edit", async () => {
    stubFetch({
      object: { document: { node: { terminals: [{ id: "access", count: 1 }] } } },
      nodes: [{ ref: NODE_REF, family: "nodes", id: "gw", display_name: "GW" }],
    });
    const g0 = newDraftGroundSet(NODE_REF, {});
    const onUpdate = vi.fn();
    render(
      <GroundEditor
        draft={g0}
        onUpdate={onUpdate}
        onRemove={() => {}}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    // Open the stamp card, then switch its node model (async fetch → onUpdate).
    fireEvent.click(screen.getByRole("button", { name: /New-site stamp/ }));
    await screen.findByRole("option", { name: "GW" }); // wait for the catalog to load the option
    fireEvent.change(screen.getByLabelText("Stamp node model"), { target: { value: NODE_REF } });
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const updater = onUpdate.mock.calls[0]![0] as (p: typeof g0) => typeof g0;

    // The user retyped the lan base DURING the fetch — only in the latest state.
    const concurrent = { ...g0, stamp: { ...g0.stamp, lan_base: "10.99" } };
    const result = updater(concurrent);
    expect(result.stamp.node_ref).toBe(NODE_REF); // the writer's own change lands
    expect(result.stamp.lan_base).toBe("10.99"); // concurrent stamp edit survives
  });

  it("GroundEditor member save-flip preserves a concurrently-added member", async () => {
    stubFetch({ nodes: [{ ref: NODE_REF, family: "nodes", id: "gw", display_name: "GW" }] });
    const g0 = newDraftGroundSet(NODE_REF, {});
    g0.members = mintSiteMembers(g0, parseSiteLines("Denver, 39.7, -104.9").rows);
    const member = g0.members[0]!;
    const onUpdate = vi.fn();
    render(
      <GroundEditor
        draft={g0}
        onUpdate={onUpdate}
        onRemove={() => {}}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    // Open the member's inline editor and save it to the library — an async save
    // whose onSaved flips the authored member to a ref (the writer).
    fireEvent.click(screen.getByRole("button", { name: `Edit ${member.label}` }));
    const embedded = screen.getByTestId("builder-site-editor");
    fireEvent.click(within(embedded).getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const updater = onUpdate.mock.calls[0]![0] as (p: typeof g0) => typeof g0;

    // A second site was pasted DURING the save — only in the latest state.
    const added = mintSiteMembers(g0, parseSiteLines("Perth, -31.9, 115.8").rows)[0]!;
    const concurrent = { ...g0, members: [...g0.members, added] };
    const result = updater(concurrent);
    const flipped = result.members.find((m) => m.member_id === member.member_id)!;
    expect(flipped.kind).toBe("ref"); // the flip lands
    expect(result.members.map((m) => m.member_id)).toContain(added.member_id); // concurrent survives
  });

  it("GroundEditor.forkMember carries the LATEST per-site scheduling, not the click-time closure", async () => {
    // The forked authored member takes its override from the matched prev member
    // — a concurrent scheduling edit made during the fork fetch survives.
    stubFetch({ object: { document: { site: siteObjectFromDraft(newDraftSiteObject(NODE_REF, {})) } } });
    const g0 = newDraftGroundSet(NODE_REF, {});
    g0.members = [refGroundMember("nodalarc:sites/denver.yaml", "denver", "Denver", null)];
    const member = g0.members[0]!;
    const onUpdate = vi.fn();
    render(
      <GroundEditor
        draft={g0}
        onUpdate={onUpdate}
        onRemove={() => {}}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: `Customize ${member.label}: fork into an editable site` }),
    );
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const updater = onUpdate.mock.calls[0]![0] as (p: typeof g0) => typeof g0;

    // The user set this member's per-site scheduling DURING the fork fetch.
    const concurrent = {
      ...g0,
      members: [{ ...member, scheduling_override: "geo-longest-pass" as const }],
    };
    const forked = updater(concurrent).members[0]!;
    expect(forked.kind).toBe("draft"); // the fork lands
    expect(forked.scheduling_override).toBe("geo-longest-pass"); // concurrent override survives
  });

  it("nested GroundEditor→SiteEditor (setNodeModel) preserves a concurrently-added member", async () => {
    // The trickiest writer: SiteEditor's functional update is threaded through
    // GroundEditor's own, applied to the member's LATEST site inside prev.members.
    stubFetch({
      object: { document: { node: { terminals: [{ id: "access", count: 1 }] } } },
      nodes: [{ ref: NODE_REF, family: "nodes", id: "gw", display_name: "GW" }],
    });
    const g0 = newDraftGroundSet(NODE_REF, {});
    g0.members = mintSiteMembers(g0, parseSiteLines("Denver, 39.7, -104.9").rows);
    const member = g0.members[0]!;
    const onUpdate = vi.fn();
    render(
      <GroundEditor
        draft={g0}
        onUpdate={onUpdate}
        onRemove={() => {}}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    // Open the member's embedded SiteEditor and switch its node model (async).
    fireEvent.click(screen.getByRole("button", { name: `Edit ${member.label}` }));
    const node0 = member.site!.nodes[0]!;
    await screen.findByRole("option", { name: "GW" });
    fireEvent.change(screen.getByLabelText(`${node0.node_id} model`), {
      target: { value: NODE_REF },
    });
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const updater = onUpdate.mock.calls[0]![0] as (p: typeof g0) => typeof g0;

    // A second site was pasted DURING the node-model fetch.
    const added = mintSiteMembers(g0, parseSiteLines("Perth, -31.9, 115.8").rows)[0]!;
    const concurrent = { ...g0, members: [...g0.members, added] };
    const result = updater(concurrent);
    const editedSite = result.members.find((m) => m.member_id === member.member_id)!.site!;
    expect(editedSite.nodes[0]!.model_ref).toBe(NODE_REF); // the nested writer's change lands
    expect(result.members.map((m) => m.member_id)).toContain(added.member_id); // concurrent survives
  });
});

// Class B writes through local React state, not the edit buffer. The leaf writer
// (NodeEditor.addOrIncrement) and one host threading (ConstellationEditor →
// NodeEditor, reading prev.node_draft) are pinned below; the library
// setLibraryEditor wrappers and NodeEditor's setTerminalDraft wrapper thread the
// same functional update through the same `prev`-read discipline.
describe("lost-edit: async writers read the latest draft (class B — through local state)", () => {
  it("NodeEditor.addOrIncrement (mounting a terminal) preserves a concurrent node-name edit", async () => {
    stubFetch({
      terminals: [{ ref: "user:terminals/ka.yaml", family: "terminals", id: "ka", display_name: "Ka" }],
    });
    const n0 = defaultDraftNode();
    const onChange = vi.fn();
    render(<NodeEditor draft={n0} onChange={onChange} />);
    // Open the port picker and mount the catalog terminal — addOrIncrement emits
    // the functional updater the async import/save paths reuse verbatim.
    fireEvent.click(screen.getByRole("button", { name: "+ port" }));
    const option = await screen.findByText(/Ka/); // the picker prefixes a ★ for user terminals
    fireEvent.click(option.closest("button")!);
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const updater = onChange.mock.calls[0]![0] as (p: typeof n0) => typeof n0;

    // The node was renamed DURING the terminal add — only in the latest state.
    const concurrent = { ...n0, display_name: "renamed", id: "renamed" };
    const result = updater(concurrent);
    expect(result.terminals.length).toBe(n0.terminals.length + 1); // the mount lands
    expect(result.display_name).toBe("renamed"); // concurrent rename survives
  });

  it("ConstellationEditor→NodeEditor threads the node update, preserving concurrent edits at both levels", async () => {
    stubFetch({
      terminals: [{ ref: "user:terminals/ka.yaml", family: "terminals", id: "ka", display_name: "Ka" }],
    });
    const c0 = newDraftConstellation(NODE_REF);
    c0.node_draft = defaultDraftNode();
    const onUpdate = vi.fn();
    render(
      <ConstellationEditor
        draft={c0}
        onUpdate={onUpdate}
        onUpdateOrbit={() => {}}
        onRemove={() => {}}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    // Open the Node card, then mount a terminal in the embedded NodeEditor —
    // NodeEditor's functional onChange is threaded through the constellation's own.
    fireEvent.click(screen.getByRole("button", { name: /Node/ }));
    fireEvent.click(screen.getByRole("button", { name: "+ port" }));
    const option = await screen.findByText(/Ka/);
    fireEvent.click(option.closest("button")!);
    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const updater = onUpdate.mock.calls[0]![0] as (p: typeof c0) => typeof c0;

    // During the mount, the user retyped planes (constellation level) AND renamed
    // the node (node level) — both live only in the latest state.
    const concurrent = {
      ...c0,
      planes: 9,
      node_draft: { ...c0.node_draft!, display_name: "renamed" },
    };
    const result = updater(concurrent);
    expect(result.node_draft!.terminals.length).toBe(c0.node_draft!.terminals.length + 1); // mount lands
    expect(result.planes).toBe(9); // concurrent constellation edit survives
    expect(result.node_draft!.display_name).toBe("renamed"); // concurrent node edit survives
  });
});

describe("reseed exception: seed-from-catalog REPLACES, stated as () => replacement", () => {
  it("TerminalEditor.seedFrom drops prev by design (a deliberate reset)", async () => {
    stubFetch({ object: { document: { terminal: { id: "ka-band", display_name: "Ka band" } } } });
    const t0 = defaultDraftTerminal();
    const onChange = vi.fn();
    const catalog = [
      {
        ref: "user:terminals/ka.yaml",
        family: "terminals",
        id: "ka",
        display_name: "Ka",
        error: null,
        notes: null,
        summary: null,
      },
    ];
    render(
      <TerminalEditor draft={t0} onChange={onChange} catalog={catalog} onSaved={() => {}} onCancel={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText("Seed terminal"), {
      target: { value: "user:terminals/ka.yaml" },
    });
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const updater = onChange.mock.calls[0]![0] as (p: typeof t0) => typeof t0;

    // Even with a concurrent edit, reseed REPLACES — the () => form ignores prev.
    const concurrent = { ...t0, display_name: "was editing", transmit_mbps: 999 };
    const result = updater(concurrent);
    expect(result.display_name).toBe("Ka band (custom)"); // replaced from the catalog
    expect(result.transmit_mbps).not.toBe(999); // the concurrent edit is intentionally dropped
  });
});
