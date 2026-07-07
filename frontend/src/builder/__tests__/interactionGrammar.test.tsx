// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Interaction-grammar conformance.
 *
 *  IG-5 static scan: builder editors compose the editor kit; a raw
 *  <input>/<select>/<textarea> in an editor file is a violation (file
 *  inputs excepted — they are not editing controls). Same enforcement
 *  pattern as the stylesheet token scan.
 *
 *  Kit behavior: EditorName create-focus (IG-2); NullableNumberField's
 *  empty-means-unset contract; EditorCard anatomy (IG-5). Object-keyed
 *  state reset (IG-4) is pinned through GroundEditor, the stateful editor.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  EditorApplyRow,
  EditorCard,
  EditorName,
  NullableNumberField,
  SliderField,
} from "../editorKit";
import { BuildGuide } from "../BuildGuide";
import { GroundEditor } from "../GroundEditor";
import {
  accessBeamElevationDeg,
  capabilitiesBySegment,
  connectSegments,
  deriveLinkPhysics,
} from "../linkPhysics";
import {
  identifier,
  mintSiteMembers,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
  parseSiteLines,
} from "../workspace";
import { canDeploy } from "../useBuilderWorld";
import {
  appliedObjectForKey,
  bufferAppliedChanged,
  overlayBuffers,
  staleBufferKeys,
  useWorkspace,
  workspaceForSave,
} from "../useWorkspace";
import type { BuilderWorld } from "../builderTypes";

/** A minimal resolved world: one ground segment with rf access mounts and
 *  a 25-degree floor; one space segment with rf access + optical isl. */
function tinyWorld(groundId: string, spaceId: string): BuilderWorld {
  const block = (role: string, medium: "rf" | "optical", elev: number | null) => ({
    terminal_id: `${role}_0`,
    owner_node_id: "n",
    endpoint_role: role,
    medium,
    source_terminal_id: null,
    link_role: null,
    count: 1,
    tracking_capacity: null,
    max_range_km: null,
    min_elevation_deg: elev,
    field_of_regard_deg: null,
    tracking_rate_deg_s: null,
    bandwidth_mbps: null,
    source_ref: "x",
  });
  const node = (id: string, segment: string, blocks: ReturnType<typeof block>[]) => ({
    node_id: id,
    local_node_id: id,
    segment_id: segment,
    namespace: null,
    kind: "satellite" as const,
    plane: null,
    slot: null,
    tags: [],
    surface_position: null,
    forwarding: null,
    terminal_inventory: blocks,
    interfaces: null,
    originated_prefixes: null,
  });
  return {
    session: { name: "t", display_name: null, description: null },
    epoch_unix: 0,
    ephemeris: { nodes: {} } as BuilderWorld["ephemeris"],
    nodes: [
      node("g1", groundId, [block("access", "rf", 25)]),
      node("s1", spaceId, [block("access", "rf", null), block("isl", "optical", null)]),
    ],
    link_rules: [],
    segments: [],
    allocations: [],
    link_candidates: [],
    rule_previews: [],
  };
}

const BUILDER_DIR = join(__dirname, "..");

describe("IG-5: editors compose the kit, never raw controls", () => {
  it("no raw input/select/textarea in any *Editor.tsx outside the kit", () => {
    const offenders: string[] = [];
    for (const file of readdirSync(BUILDER_DIR)) {
      if (!file.endsWith("Editor.tsx")) continue;
      const source = readFileSync(join(BUILDER_DIR, file), "utf-8");
      const lines = source.split("\n");
      lines.forEach((line, index) => {
        if (!/<(input|select|textarea)\b/.test(line)) return;
        // File pickers are not editing controls; the attribute may sit on a
        // following line of the same JSX element.
        const window = lines.slice(index, index + 4).join(" ");
        if (/type="file"/.test(window)) return;
        offenders.push(`${file}:${index + 1}: ${line.trim()}`);
      });
    }
    expect(
      offenders,
      "Raw editing controls bypass the interaction grammar (use editorKit):\n" +
        offenders.join("\n"),
    ).toHaveLength(0);
  });
});

describe("editor kit behavior", () => {
  afterEach(cleanup);

  it("IG-2: EditorName focuses and selects on create", () => {
    render(<EditorName value="seeded name" onChange={() => {}} autoFocus />);
    const input = screen.getByDisplayValue("seeded name");
    expect(document.activeElement).toBe(input);
  });

  it("EditorName does not steal focus when not fresh", () => {
    render(<EditorName value="seeded name" onChange={() => {}} />);
    expect(document.activeElement).not.toBe(screen.getByDisplayValue("seeded name"));
  });

  it("NullableNumberField: empty means unset, never zero", () => {
    let value: number | null = 25;
    render(
      <NullableNumberField
        label="min elevation"
        placeholder="none"
        value={value}
        onChange={(v) => {
          value = v;
        }}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("none"), { target: { value: "" } });
    expect(value).toBeNull();
  });

  it("IG-5: EditorCard closed reads as spec (summary), open shows the body", () => {
    const { rerender } = render(
      <EditorCard title="Orbit" summary="550 km circular" open={false} onToggle={() => {}}>
        <div data-testid="body" />
      </EditorCard>,
    );
    expect(screen.getByText("550 km circular")).toBeTruthy();
    expect(screen.queryByTestId("body")).toBeNull();
    rerender(
      <EditorCard title="Orbit" summary="550 km circular" open onToggle={() => {}}>
        <div data-testid="body" />
      </EditorCard>,
    );
    expect(screen.getByTestId("body")).toBeTruthy();
  });
});

describe("IG-7: connect derives physics from faceplates", () => {
  function connectWorkspace() {
    const workspace = newWorkspace("t");
    workspace.space.push(newDraftConstellation("nodalarc:nodes/space/leo-relay.yaml"));
    const ground = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    ground.members = mintSiteMembers(ground, parseSiteLines("Denver, 39.7, -104.9").rows);
    workspace.ground.push(ground);
    return { workspace, groundId: ground.segment_id, spaceId: workspace.space[0]!.segment_id };
  }

  it("ground-to-space derives access, the mask from the ground terminals", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId);
    const rule = connectSegments(workspace, world, groundId, spaceId);
    expect(rule.a.role).toBe("access");
    expect(rule.a.medium).toBe("rf");
    // The mask comes from the ground segment's own access terminals.
    expect(rule.a.min_elevation_deg).toBe(25);
    expect(rule.b.min_elevation_deg).toBeNull();
    expect(rule.topology_mode).toBe("visible_candidates");
  });

  it("self-connect derives the fabric: isl, optical, nearest-2", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId);
    const rule = connectSegments(workspace, world, spaceId, spaceId);
    expect(rule.a.role).toBe("isl");
    expect(rule.a.medium).toBe("optical");
    expect(rule.topology_mode).toBe("nearest_n");
    expect(rule.topology_n).toBe(2);
  });

  it("unformable pairs say so instead of inventing physics", () => {
    const { groundId, spaceId } = connectWorkspace();
    const capabilities = capabilitiesBySegment(tinyWorld(groundId, spaceId));
    // The ground segment has no crosslink terminals: space-to-space
    // derivation against it must report formable=false.
    const physics = deriveLinkPhysics(
      capabilities,
      { segment_id: spaceId, kind: "space" },
      { segment_id: groundId, kind: "space" }, // pretend both space
    );
    expect(physics.formable).toBe(false);
  });
});

describe("IG-4: editor state is keyed by object identity", () => {
  beforeEach(() => {
    // The catalog fetches behind useBuilderCatalog are irrelevant here.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ entries: [] }),
      })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("switching objects yields the canonical presentation, not the last one's", () => {
    const a = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    const b = newDraftGroundSet("nodalarc:nodes/ground/leo-gateway.yaml", {});
    const shared = {
      workspace: newWorkspace("t"),
      onOpenRule: () => {},
      onConnect: () => {},
      onUpdate: () => {},
      onRemove: () => {},
    };
    const { rerender } = render(
      <GroundEditor key={a.segment_id} draft={a} {...shared} />,
    );
    // Canonical: Sites open. Toggle it closed — a per-object view state.
    fireEvent.click(screen.getByText("Sites"));
    expect(screen.queryByText("+ mint pasted sites")).toBeNull();
    // Switch to object B (new key = remount): canonical again, no bleed.
    rerender(<GroundEditor key={b.segment_id} draft={b} {...shared} />);
    expect(screen.getByText("+ mint pasted sites")).toBeTruthy();
  });
});

describe("IG-14: buffered windows commit through the apply row", () => {
  afterEach(cleanup);

  it("a clean window says applied; Apply and Defaults are disabled", () => {
    render(
      <EditorApplyRow
        dirty={false}
        onApply={() => {}}
        onOk={() => {}}
        onDefaults={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText("applied")).toBeTruthy();
    expect((screen.getByText("Apply") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByText("Defaults") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByText("Cancel") as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByText("OK") as HTMLButtonElement).disabled).toBe(false);
  });

  it("a dirty window says so and every commit path fires its own callback", () => {
    const calls: string[] = [];
    render(
      <EditorApplyRow
        dirty
        onApply={() => calls.push("apply")}
        onOk={() => calls.push("ok")}
        onDefaults={() => calls.push("defaults")}
        onCancel={() => calls.push("cancel")}
      />,
    );
    expect(screen.getByText("unapplied changes")).toBeTruthy();
    expect(screen.queryByTestId("builder-stale-notice")).toBeNull();
    fireEvent.click(screen.getByText("Apply"));
    fireEvent.click(screen.getByText("OK"));
    fireEvent.click(screen.getByText("Defaults"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(calls).toEqual(["apply", "ok", "defaults", "cancel"]);
  });

  // M5: a window whose applied object moved underneath a dirty working copy
  // shows the stale notice and offers to reload current values. Apply stays
  // live — keeping the edits is the user's call — but the bulk save refuses.
  it("a stale window shows the notice and Load current values fires its own path", () => {
    const calls: string[] = [];
    render(
      <EditorApplyRow
        dirty
        stale
        onApply={() => calls.push("apply")}
        onOk={() => calls.push("ok")}
        onDefaults={() => calls.push("defaults")}
        onLoadCurrent={() => calls.push("load")}
        onCancel={() => calls.push("cancel")}
      />,
    );
    expect(screen.getByTestId("builder-stale-notice")).toBeTruthy();
    expect(screen.getByText("stale")).toBeTruthy();
    // Apply is deliberately still enabled — the edits may still be wanted.
    expect((screen.getByText("Apply") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByText("Load current values"));
    expect(calls).toEqual(["load"]);
  });
});

describe("beam footprints read the terminals, never a default", () => {
  const world = tinyWorld("gnd", "shell");

  it("a declared access floor is the beam's floor; the strictest wins", () => {
    const node = structuredClone(world.nodes.find((n) => n.segment_id === "gnd")!);
    node.terminal_inventory[0]!.min_elevation_deg = 10;
    node.terminal_inventory.push({
      ...node.terminal_inventory[0]!,
      terminal_id: "access_1",
      min_elevation_deg: 30,
    });
    expect(accessBeamElevationDeg(node)).toBe(30);
  });

  it("an access terminal with no declared floor serves to the horizon", () => {
    const node = structuredClone(world.nodes.find((n) => n.segment_id === "shell")!);
    for (const block of node.terminal_inventory) block.min_elevation_deg = null;
    expect(accessBeamElevationDeg(node)).toBe(0);
  });

  it("no access terminal means no beam, not an invented one", () => {
    const node = structuredClone(world.nodes.find((n) => n.segment_id === "shell")!);
    node.terminal_inventory = node.terminal_inventory.filter(
      (b) => b.endpoint_role !== "access",
    );
    expect(accessBeamElevationDeg(node)).toBe(null);
  });
});

describe("SliderField: track for the common range, box for the truth", () => {
  afterEach(cleanup);

  it("typing past the track is allowed and reported verbatim", () => {
    const seen: number[] = [];
    render(
      <SliderField
        label="altitude"
        value={550}
        min={150}
        max={40000}
        onChange={(v) => seen.push(v)}
      />,
    );
    const box = document.querySelector('input[type="number"]') as HTMLInputElement;
    fireEvent.change(box, { target: { value: "120000" } });
    expect(seen).toEqual([120000]);
  });

  it("the slider itself streams values and clamps its display to the track", () => {
    const seen: number[] = [];
    const { rerender } = render(
      <SliderField
        label="altitude"
        value={550}
        min={150}
        max={40000}
        onChange={(v) => seen.push(v)}
      />,
    );
    const track = screen.getByLabelText("altitude slider") as HTMLInputElement;
    fireEvent.change(track, { target: { value: "36000" } });
    expect(seen).toEqual([36000]);
    rerender(
      <SliderField
        label="altitude"
        value={120000}
        min={150}
        max={40000}
        onChange={(v) => seen.push(v)}
      />,
    );
    expect(track.value).toBe("40000");
  });
});

describe("IG-15: the anatomy guide answers what-next in any order", () => {
  afterEach(cleanup);
  const guideProps = (ws: ReturnType<typeof newWorkspace>) => ({
    workspace: ws,
    saved: null,
    deployed: false,
    onAddConstellation: () => {},
    onAddGround: () => {},
    onAddDomain: () => {},
    onOpenSession: () => {},
    onOpenSegment: () => {},
  });

  it("every anatomy row is always on screen, pending or done", () => {
    render(<BuildGuide {...guideProps(newWorkspace("untitled-session"))} />);
    for (const label of [
      "Space segments",
      "Ground sites",
      "Comms intent",
      "Routing",
      "Identity & time",
      "Save & deploy",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("pending rows say why and act; gated rows say what unblocks them", () => {
    const calls: string[] = [];
    const ws = newWorkspace("untitled-session");
    render(
      <BuildGuide
        {...guideProps(ws)}
        onAddConstellation={() => calls.push("space")}
      />,
    );
    fireEvent.click(screen.getByText("Space segments"));
    expect(calls).toEqual(["space"]);
    // No segments yet: comms intent explains its precondition instead of acting.
    expect(screen.getByText("needs two segments first")).toBeTruthy();
    expect(screen.getByText("add links first")).toBeTruthy();
  });

  it("done rows show counts, not health claims", () => {
    const ws = newWorkspace("named-session");
    ws.space.push(newDraftConstellation("nodalarc:nodes/space/x.yaml"));
    render(<BuildGuide {...guideProps(ws)} />);
    expect(screen.getByText("1 segment · add more")).toBeTruthy();
    expect(screen.getByText("named-session")).toBeTruthy();
  });
});

describe("IG-16: the closed vocabularies have one owner", () => {
  const vocabularyOffenders = (pattern: RegExp): string[] => {
    const offenders: string[] = [];
    for (const entry of readdirSync(BUILDER_DIR)) {
      if (!entry.endsWith(".ts") && !entry.endsWith(".tsx")) continue;
      if (entry === "workspace.ts") continue; // the owner
      const content = readFileSync(join(BUILDER_DIR, entry), "utf-8");
      if (pattern.test(content)) offenders.push(entry);
    }
    return offenders;
  };

  it("no builder source file re-lists the mount roles as an array literal", () => {
    // An array literal holding two or more role tokens is a vocabulary
    // copy; single tokens as object values (preference tables) are
    // consumption of the vocabulary and pass.
    expect(
      vocabularyOffenders(
        /\[\s*"(access|isl|crosslink|backbone)"\s*,\s*"(access|isl|crosslink|backbone)"/,
      ),
      "role vocabulary re-listed outside workspace.ts",
    ).toEqual([]);
  });

  it("no builder source file re-lists the link media as an array literal", () => {
    expect(
      vocabularyOffenders(/\[\s*"(rf|optical)"\s*,\s*"(rf|optical)"/),
      "media vocabulary re-listed outside workspace.ts",
    ).toEqual([]);
  });
});

describe("IG-17: a save is never a dead end", () => {
  it("every library save announces the asset through the reveal store", async () => {
    const { requestLibraryReveal, useLibraryReveal } = await import("../useBuilderWorld");
    let latest: unknown = null;
    function Probe() {
      latest = useLibraryReveal();
      return null;
    }
    render(<Probe />);
    const entry = {
      ref: "user:terminals/test-radio.yaml",
      family: "terminals",
      id: "test-radio",
      display_name: "Test radio",
      notes: null,
      summary: null,
      error: null,
    };
    act(() => requestLibraryReveal(entry));
    expect((latest as { entry: typeof entry }).entry.ref).toBe(
      "user:terminals/test-radio.yaml",
    );
  });

  it("the toolbar owns the session verbs as icon buttons; the rail owns none", () => {
    const source = readFileSync(join(BUILDER_DIR, "BuilderView.tsx"), "utf-8");
    const toolbar = source.slice(
      source.indexOf('className="builder-toolbar"'),
      source.indexOf('className="builder-outline"'),
    );
    // Icon-only: each verb is an icon with a hover/aria label, not visible text.
    for (const glyph of ["file-plus", "folder-open", "save", "rocket", "history", "library"]) {
      expect(toolbar, `toolbar carries the ${glyph} glyph`).toContain(`icon="${glyph}"`);
    }
    // Open and Save are windows (pickers), not an inline dropdown.
    expect(toolbar).toContain('kind: "open-session"');
    expect(toolbar).toContain('kind: "save-session"');
    expect(toolbar, "no inline session dropdown in the toolbar").not.toContain(
      'aria-label="Catalog session"',
    );
    const rail = source.slice(
      source.indexOf('className="builder-outline"'),
      source.indexOf('className="builder-canvas"'),
    );
    for (const verb of ["Save session", "Deploy to cluster", "Library…"]) {
      expect(rail, `rail must not carry "${verb}" as a control`).not.toContain(`>${verb}<`);
    }
  });

  it("each reveal consumer role claims a nonce once, across remounts", async () => {
    const { claimLibraryReveal } = await import("../useBuilderWorld");
    const entry = {
      ref: "user:terminals/claim-probe.yaml",
      family: "terminals",
      id: "claim-probe",
      display_name: "Claim probe",
      notes: null,
      summary: null,
      error: null,
    };
    // The registry is module state — a remounted consumer re-running its
    // effect is exactly a second claim of the same nonce, and must get null
    // (per-mount refs replayed the last save on every remount).
    const first = claimLibraryReveal("opener", { entry, nonce: 2_000_001 });
    expect(first?.entry.ref).toBe("user:terminals/claim-probe.yaml");
    expect(claimLibraryReveal("opener", { entry, nonce: 2_000_001 })).toBeNull();
    // Roles retire independently — a late-mounting lander still lands.
    expect(claimLibraryReveal("lander", { entry, nonce: 2_000_001 })).not.toBeNull();
    expect(claimLibraryReveal("lander", { entry, nonce: 2_000_001 })).toBeNull();
    // A newer save claims again; null reveals never claim.
    expect(claimLibraryReveal("opener", { entry, nonce: 2_000_002 })).not.toBeNull();
    expect(claimLibraryReveal("opener", null)).toBeNull();
  });
});

describe("deploy gate: artifact truth, runtime-readiness, fail closed", () => {
  const saved = {
    savedFile: "/data/generated-sessions/_builder-x.yaml",
    savedArtifactSha256: "abc",
  };
  const ready = { deployReady: true, deployBlockers: [] as string[] };

  it("deploys only when the saved artifact matches the settled resolve", () => {
    expect(
      canDeploy({ ...saved, ...ready, settledArtifactSha256: "abc", dirtyWindowCount: 0 }),
    ).toEqual({ ok: true, reason: null });
  });

  it("refuses without a save", () => {
    const gate = canDeploy({
      savedFile: null,
      savedArtifactSha256: null,
      settledArtifactSha256: "abc",
      dirtyWindowCount: 0,
      ...ready,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/save the session first/);
  });

  it("fails closed when no resolve has settled (cleared or refused)", () => {
    const gate = canDeploy({
      ...saved,
      ...ready,
      settledArtifactSha256: null,
      dirtyWindowCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/must resolve/);
  });

  it("refuses a saved, settled session that cannot start on the cluster (Q3)", () => {
    // Every artifact/dirty check passes, but the session is not runtime-ready.
    const gate = canDeploy({
      ...saved,
      settledArtifactSha256: "abc",
      dirtyWindowCount: 0,
      deployReady: false,
      deployBlockers: ["no satellites — the session cannot start on the cluster"],
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/no satellites/);
  });

  it("refuses while windows hold unapplied edits", () => {
    const gate = canDeploy({
      ...saved,
      ...ready,
      settledArtifactSha256: "abc",
      dirtyWindowCount: 2,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/2 windows with unapplied edits/);
  });

  it("names the staleness when the saved copy is behind the edits", () => {
    const gate = canDeploy({
      ...saved,
      ...ready,
      settledArtifactSha256: "def",
      dirtyWindowCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/behind your edits/);
  });
});

describe("commitWorkspace: one atomic adoption, one undo entry", () => {
  it("undo after commitWorkspace returns exactly to the pre-commit state", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.startNew("alpha"));
    const before = result.current.workspace;
    expect(before?.name).toBe("alpha");
    act(() =>
      result.current.commitWorkspace({ ...before!, name: "beta" }, "test-adoption"),
    );
    expect(result.current.workspace?.name).toBe("beta");
    act(() => result.current.undo());
    expect(result.current.workspace?.name).toBe("alpha");
  });
});

describe("buffer overlays and the stale guard", () => {
  it("overlayBuffers substitutes only dirty working copies", () => {
    const ws = newWorkspace("overlay-probe");
    const draft = newDraftConstellation("nodalarc:nodes/x.yaml");
    ws.space = [draft];
    const edited = { ...draft, label: "edited" };
    const clean = overlayBuffers(ws, {
      [`segment:${draft.segment_id}`]: { draft: edited, opened: draft, dirty: false },
    });
    expect(clean).toBe(ws);
    const overlaid = overlayBuffers(ws, {
      [`segment:${draft.segment_id}`]: { draft: edited, opened: draft, dirty: true },
    });
    expect(overlaid.space[0]).toBe(edited);
    // The session buffer is a field pick, never the whole workspace.
    const renamed = overlayBuffers(ws, {
      session: { draft: { name: "picked" }, opened: { name: ws.name }, dirty: true },
    });
    expect(renamed.name).toBe("picked");
    expect(renamed.space).toBe(ws.space);
  });

  it("a dirty buffer is stale once its applied object changed underneath", () => {
    const ws = newWorkspace("stale-probe");
    const draft = newDraftConstellation("nodalarc:nodes/x.yaml");
    ws.space = [draft];
    const key = `segment:${draft.segment_id}`;
    const buffer = {
      draft: { ...draft, label: "working copy" },
      opened: structuredClone(draft),
      dirty: true,
    };
    const buffers = { [key]: buffer };
    // Applied still equals the opened base: not stale.
    expect(staleBufferKeys(ws, buffers)).toEqual([]);
    // Undo/restore changed the applied object underneath the window.
    const undone = { ...ws, space: [{ ...draft, label: "reverted elsewhere" }] };
    expect(staleBufferKeys(undone, buffers)).toEqual([key]);
    // Deletion underneath is NOT "stale": the reconciliation pass prunes the
    // window and its buffer, so no window is left to carry a notice and there is
    // nothing to apply into. Prune (gone) and stale (moved-and-present) are
    // disjoint so the two owners can never disagree.
    const deleted = { ...ws, space: [] };
    expect(staleBufferKeys(deleted, buffers)).toEqual([]);
    // Clean buffers are never stale.
    expect(staleBufferKeys(undone, { [key]: { ...buffer, dirty: false } })).toEqual([]);
  });

  it("the session-pick buffer compares only its own fields", () => {
    const ws = newWorkspace("session-pick-probe");
    const buffers = {
      session: {
        draft: { name: "typed" },
        opened: { name: ws.name },
        dirty: true,
      },
    };
    expect(staleBufferKeys(ws, buffers)).toEqual([]);
    expect(staleBufferKeys({ ...ws, name: "renamed-by-undo" }, buffers)).toEqual([
      "session",
    ]);
  });

  // M5: the reconciliation pass decides prune/drop through the same two
  // primitives the stale guard uses — never a second key->object resolver.
  it("appliedObjectForKey resolves object kinds by id, and null everywhere else", () => {
    const ws = newWorkspace("resolve-probe");
    const seg = newDraftConstellation("nodalarc:nodes/x.yaml");
    ws.space = [seg];
    expect(appliedObjectForKey(ws, `segment:${seg.segment_id}`)).toBe(seg);
    // Gone object -> null: this is exactly the prune trigger.
    expect(appliedObjectForKey(ws, "segment:missing")).toBeNull();
    // The session kind names a field pick, not a single object; the chrome and
    // read-only kinds own no object at all.
    expect(appliedObjectForKey(ws, "session")).toBeNull();
    expect(appliedObjectForKey(ws, "library")).toBeNull();
    expect(appliedObjectForKey(ws, "node:sat-1")).toBeNull();
  });

  it("bufferAppliedChanged is the one primitive under staleness and the clean drop", () => {
    const ws = newWorkspace("changed-probe");
    const seg = newDraftConstellation("nodalarc:nodes/x.yaml");
    ws.space = [seg];
    const key = `segment:${seg.segment_id}`;
    const buf = {
      draft: { ...seg, label: "working copy" },
      opened: structuredClone(seg),
      dirty: true,
    };
    // Applied equals opened: unchanged, whatever the dirty flag says.
    expect(bufferAppliedChanged(ws, key, buf)).toBe(false);
    expect(bufferAppliedChanged(ws, key, { ...buf, dirty: false })).toBe(false);
    // Applied moved underneath (undo, sibling edit) or gone (deletion).
    const moved = { ...ws, space: [{ ...seg, label: "moved elsewhere" }] };
    expect(bufferAppliedChanged(moved, key, buf)).toBe(true);
    expect(bufferAppliedChanged({ ...ws, space: [] }, key, buf)).toBe(true);
    // The dirty flag does not enter the primitive: a CLEAN buffer over a moved
    // object is "changed" too — that is the clean-drop trigger, the mirror of
    // the dirty-only staleBufferKeys projection.
    expect(bufferAppliedChanged(moved, key, { ...buf, dirty: false })).toBe(true);
    expect(staleBufferKeys(moved, { [key]: buf })).toEqual([key]);
    expect(staleBufferKeys(moved, { [key]: { ...buf, dirty: false } })).toEqual([]);
  });

  // M5 bulk apply-and-save: the confirm flow DECLINES a stale window by leaving
  // its key out of the overlay, so an unconfirmed working copy is never written.
  it("overlayBuffers skips declined keys; the rest apply", () => {
    const ws = newWorkspace("skip-probe");
    const a = newDraftConstellation("nodalarc:nodes/a.yaml");
    const b = newDraftConstellation("nodalarc:nodes/b.yaml");
    ws.space = [a, b];
    const editedA = { ...a, label: "A!" };
    const editedB = { ...b, label: "B!" };
    const buffers = {
      [`segment:${a.segment_id}`]: { draft: editedA, opened: a, dirty: true },
      [`segment:${b.segment_id}`]: { draft: editedB, opened: b, dirty: true },
    };
    const out = overlayBuffers(ws, buffers, new Set([`segment:${b.segment_id}`]));
    expect(out.space.find((d) => d.segment_id === a.segment_id)).toBe(editedA);
    // B was declined — its applied object is untouched.
    expect(out.space.find((d) => d.segment_id === b.segment_id)).toBe(b);
  });

  it("workspaceForSave threads excludeKeys into the apply-all overlay", () => {
    const ws = newWorkspace("exclude-probe");
    const a = newDraftConstellation("nodalarc:nodes/a.yaml");
    ws.space = [a];
    const editedA = { ...a, label: "A!" };
    const key = `segment:${a.segment_id}`;
    const buffers = { [key]: { draft: editedA, opened: a, dirty: true } };
    // Declined: the applied object survives even under applyAll.
    const excluded = workspaceForSave(ws, buffers, {
      applyAll: true,
      dialogName: "",
      nameTouched: false,
      excludeKeys: new Set([key]),
    });
    expect(excluded.space[0]).toBe(a);
    // Confirmed (nothing excluded): the working copy applies.
    const applied = workspaceForSave(ws, buffers, {
      applyAll: true,
      dialogName: "",
      nameTouched: false,
      excludeKeys: new Set(),
    });
    expect(applied.space[0]).toBe(editedA);
  });
});

describe("save dialog: the name commits once, never per keystroke", () => {
  it("BuilderView no longer live-writes the workspace name from the dialog", () => {
    const source = readFileSync(join(BUILDER_DIR, "BuilderView.tsx"), "utf-8");
    // The old dialog normalized and committed on every keystroke; the name
    // is buffered in SaveSessionDialog and identifier() runs at save.
    expect(source).not.toContain("updateSession({ name: identifier(name)");
    const dialog = source.slice(
      source.indexOf("function SaveSessionDialog"),
      source.indexOf("export function BuilderView"),
    );
    expect(dialog, "the dialog buffers its name locally").toContain(
      "useState(workspaceName)",
    );
    expect(dialog, "the dirty-save primary applies first").toContain("applyAll: true");
  });

  it("save applied state only is never gated by the dirty preview", () => {
    const source = readFileSync(join(BUILDER_DIR, "BuilderView.tsx"), "utf-8");
    const dialog = source.slice(
      source.indexOf("function SaveSessionDialog"),
      source.indexOf("export function BuilderView"),
    );
    // The preview gate (canSave) belongs to every SAVE primary — apply-and-save,
    // the no-dirty-windows Save, and the stale-confirm view's overwrite-and-save
    // — three occurrences. The applied-only escape hatch attempts regardless
    // (the server owns the applied session's verdict once dirty windows diverge
    // the preview) and is held back only by an in-flight save.
    expect(dialog.match(/disabled=\{!canSave/g)?.length).toBe(3);
    expect(dialog, "applied-only disabled by saving alone").toContain(
      "disabled={saving}",
    );
  });
});

describe("open picker: source is the server's word, not a path sniff", () => {
  it("BuilderView groups by entry.source and never sniffs the file path", () => {
    const source = readFileSync(join(BUILDER_DIR, "BuilderView.tsx"), "utf-8");
    // The server names each entry's root tier; client-side knowledge of
    // the server's directory layout was the contract drift being removed.
    expect(source).not.toContain("generated-sessions");
    expect(source).toContain('s.source === "user"');
    expect(source).toContain('s.source === "nodalarc"');
    // The tiers speak the library's own vocabulary.
    expect(source).toContain('group("★ yours"');
    expect(source).toContain('group("nodalarc library"');
  });
});

describe("workspaceForSave: the dialog name never silently undoes a rename", () => {
  it("apply-and-save keeps a dirty Session-window rename when the field is untouched", () => {
    const ws = newWorkspace("old-name");
    const buffers = {
      session: {
        draft: { name: "renamed-in-session" },
        opened: { name: "old-name" },
        dirty: true,
      },
    };
    const next = workspaceForSave(ws, buffers, {
      applyAll: true,
      dialogName: "old-name",
      nameTouched: false,
    });
    expect(next.name).toBe("renamed-in-session");
  });

  it("a name the user actually typed in the dialog wins over the overlays", () => {
    const ws = newWorkspace("old-name");
    const buffers = {
      session: {
        draft: { name: "renamed-in-session" },
        opened: { name: "old-name" },
        dirty: true,
      },
    };
    const next = workspaceForSave(ws, buffers, {
      applyAll: true,
      dialogName: "typed name",
      nameTouched: true,
    });
    expect(next.name).toBe(identifier("typed name"));
  });

  it("applied-only with an untouched field is the identity", () => {
    const ws = newWorkspace("applied-name");
    const next = workspaceForSave(ws, {}, {
      applyAll: false,
      dialogName: "applied-name",
      nameTouched: false,
    });
    expect(next).toBe(ws);
  });

  it("a touched but empty name falls back to the base name, never an empty id", () => {
    const ws = newWorkspace("kept");
    const next = workspaceForSave(ws, {}, {
      applyAll: false,
      dialogName: "",
      nameTouched: true,
    });
    expect(next.name).toBe("kept");
  });
});
