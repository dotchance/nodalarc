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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  mintSiteMembers,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
  parseSiteLines,
} from "../workspace";
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
    fireEvent.click(screen.getByText("Apply"));
    fireEvent.click(screen.getByText("OK"));
    fireEvent.click(screen.getByText("Defaults"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(calls).toEqual(["apply", "ok", "defaults", "cancel"]);
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
