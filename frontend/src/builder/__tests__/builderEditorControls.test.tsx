// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder editor control conformance.
 *
 *  static scan: builder editors compose the editor kit; a raw
 *  <input>/<select>/<textarea> in an editor file is a violation (file
 *  inputs excepted — they are not editing controls). Same enforcement
 *  pattern as the stylesheet token scan.
 *
 *  Kit behavior: EditorName create-focus; NullableNumberField's
 *  empty-means-unset contract; EditorCard anatomy. Object-keyed
 *  state reset is tested through GroundEditor, the stateful editor.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BodySelect,
  EditorApplyRow,
  EditorCard,
  EditorName,
  NumberField,
  NullableNumberField,
  SelectField,
  SliderField,
} from "../editorKit";
import { BuildGuide } from "../BuildGuide";
import { GroundEditor } from "../GroundEditor";
import { SiteEditor } from "../SiteEditor";
import { ConstellationEditor } from "../ConstellationEditor";
import {
  accessBeamElevationDeg,
} from "../linkPhysics";
import { type DraftSiteObject } from "../workspace";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";
import {
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
  testGroundMember,
} from "./fixtures/workspaceFixtures";
import type { CatalogDocumentSummary } from "../generated/builderApi";
import { canDeploy } from "../useBuilderWorld";
import {
  appliedObjectForKey,
  bufferAppliedChanged,
  overlayBuffers,
  staleBufferKeys,
  useWorkspace,
  workspaceForSave,
} from "../useWorkspace";
import { tinyWorld } from "./fixtures/tinyWorld";

const BUILDER_DIR = join(__dirname, "..");

function draftSite(nodeRef = "nodalarc:nodes/ground/gw.yaml"): DraftSiteObject {
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
      model_ref: nodeRef,
      installed: {},
      boresights: {},
      lo0_ipv4: "10.200.0.1/32",
      terr0_ipv4: "172.20.0.1/24",
    }],
  };
}

function catalogSummary(
  ref: string,
  family: CatalogDocumentSummary["family"],
  displayName: string,
): CatalogDocumentSummary {
  return {
    ref,
    family,
    namespace: ref.startsWith("user:") ? "user" : "nodalarc",
    revision: `revision-${ref}`,
    size_bytes: 100,
    display_name: displayName,
    summary: null,
  };
}

type DeepMutable<T> = T extends ReadonlyArray<infer Item>
  ? DeepMutable<Item>[]
  : T extends object
    ? { -readonly [Key in keyof T]: DeepMutable<T[Key]> }
    : T;

function mutableClone<T>(value: T): DeepMutable<T> {
  return structuredClone(value) as DeepMutable<T>;
}

describe("builder surfaces compose the kit, never raw controls", () => {
  // The kit DEFINES the controls, so editorKit.tsx is exempt. __tests__ is a
  // directory, skipped by the extension filter (enumerated for intent). The scan
  // is non-recursive: production builder files are FLAT (only __tests__ is a
  // subdirectory) — a future production subdir would need this widened.
  //
  // Attribute exemptions, scoped to the CONTROL'S OWN opening tag so a sibling
  // element's attribute never exempts a raw control:
  //   - type="file" everywhere: a file picker is not an editing control.
  //   - type="checkbox" only OUTSIDE an *Editor.tsx: the kit DOES own checkbox
  //     anatomy (CheckboxField), so an editor's boolean object field must use
  //     it; the shell's transient confirmation checkboxes (the save-stale
  //     dialog) are not editor object fields and legitimately stay raw.
  const EXEMPT_FILES = new Set(["editorKit.tsx"]);
  it("no raw input/select/textarea in any production builder .tsx outside the kit", () => {
    const offenders: string[] = [];
    for (const file of readdirSync(BUILDER_DIR)) {
      if (file === "__tests__") continue;
      if (!file.endsWith(".tsx")) continue;
      if (EXEMPT_FILES.has(file)) continue;
      const isEditor = file.endsWith("Editor.tsx");
      const lines = readFileSync(join(BUILDER_DIR, file), "utf-8").split("\n");
      lines.forEach((line, index) => {
        if (!/<(input|select|textarea)\b/.test(line)) return;
        // Accumulate only THIS element's opening tag — from the match to the
        // line that ends the tag (`>` or `/>` at line end) — so the exempt
        // attribute must belong to the matched control, not a nearby sibling.
        let tag = "";
        for (let i = index; i < lines.length && i < index + 10; i++) {
          tag += `${lines[i]} `;
          if (/>\s*$/.test(lines[i]!)) break;
        }
        if (/type="file"/.test(tag)) return;
        if (/type="checkbox"/.test(tag) && !isEditor) return;
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

describe("card anatomy lives only in the kit (EditorCard)", () => {
  it("no builder-card* token in any production builder file outside the kit", () => {
    // The whole builder-card family — builder-card, -head, -title, -summary,
    // -body. They share the "builder-card" prefix, so one match closes the class
    // (banning a single token would under-close it). A hit outside editorKit.tsx
    // is a hand-rolled card that must compose EditorCard instead.
    const offenders: string[] = [];
    for (const file of readdirSync(BUILDER_DIR)) {
      if (file === "__tests__") continue;
      if (!file.endsWith(".ts") && !file.endsWith(".tsx")) continue;
      if (file === "editorKit.tsx") continue; // the kit DEFINES the card anatomy
      const lines = readFileSync(join(BUILDER_DIR, file), "utf-8").split("\n");
      lines.forEach((line, index) => {
        if (/builder-card/.test(line)) offenders.push(`${file}:${index + 1}: ${line.trim()}`);
      });
    }
    expect(
      offenders,
      "Hand-rolled card anatomy bypasses EditorCard (compose the kit):\n" + offenders.join("\n"),
    ).toHaveLength(0);
  });
});

describe("EditorCard adoption smoke: current editors render and their cards behave", () => {
  beforeEach(() => {
    // The editors read catalogs on mount; the endpoint returns a bare array.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ generation: "g1", items: [] }) })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("SiteEditor: a multi-node site card carries a working Remove (the actions slot)", () => {
    const base = draftSite();
    const site = { ...base, nodes: [base.nodes[0]!, { ...base.nodes[0]!, node_id: "gw2" }] };
    let updated = site;
    render(
      <SiteEditor
        authoring={AUTHORING_FACTS}
        site={site}
        onSetNodeModel={async () => {}}
        onAddNode={async () => {}}
        onUpdate={(update) => {
          updated = update(updated);
        }}
      />,
    );
    // Both node cards render (the card title is the node id).
    expect(screen.getByText("gw1")).toBeTruthy();
    expect(screen.getByText("gw2")).toBeTruthy();
    // The header Remove — carried through EditorCard's actions slot — drops its node.
    fireEvent.click(screen.getByRole("button", { name: "Remove gw2" }));
    expect(updated.nodes.map((n) => n.node_id)).toEqual(["gw1"]);
  });

  it("ConstellationEditor: a collapsed accordion card opens on head click", () => {
    render(
      <ConstellationEditor
        authoring={AUTHORING_FACTS}
        draft={newDraftConstellation("nodalarc:nodes/space/leo.yaml")}
        workspace={newWorkspace("t")}
        onUpdate={() => {}}
        onUpdateOrbit={() => {}}
        onSetPopulation={async () => {}}
        onAuthorInlineNode={async () => {}}
        onAddNodeTerminal={async () => {}}
        onSetNodeTerminalRole={async () => {}}
        onAddNodeEthernet={async () => {}}
        onRemove={() => {}}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    // Orbit is open by default; Pattern is collapsed, so its body field is hidden.
    expect(screen.queryByText("planes")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Pattern/ }));
    expect(screen.getByText("planes")).toBeTruthy();
  });

  it("ConstellationEditor delegates phasing transitions without authoring defaults", async () => {
    const draft = newDraftConstellation("nodalarc:nodes/space/leo.yaml");
    const onUpdate = vi.fn();
    const onSetPopulation = vi.fn(async () => {});
    render(
      <ConstellationEditor
        authoring={AUTHORING_FACTS}
        draft={draft}
        workspace={newWorkspace("t")}
        onUpdate={onUpdate}
        onUpdateOrbit={() => {}}
        onSetPopulation={onSetPopulation}
        onAuthorInlineNode={async () => {}}
        onAddNodeTerminal={async () => {}}
        onSetNodeTerminalRole={async () => {}}
        onAddNodeEthernet={async () => {}}
        onRemove={() => {}}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Pattern/ }));
    fireEvent.change(screen.getByLabelText("phasing"), {
      target: { value: "evenly_spaced_mean_anomaly" },
    });
    await waitFor(() => expect(onSetPopulation).toHaveBeenCalledTimes(1));
    expect(onSetPopulation).toHaveBeenCalledWith({
      phasing_mode: "evenly_spaced_mean_anomaly",
    });
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("ConstellationEditor delegates inline node creation without browser-generated fields", async () => {
    const onUpdate = vi.fn();
    const onAuthorInlineNode = vi.fn(async () => {});
    render(
      <ConstellationEditor
        authoring={AUTHORING_FACTS}
        draft={newDraftConstellation("nodalarc:nodes/space/leo.yaml")}
        workspace={newWorkspace("t")}
        onUpdate={onUpdate}
        onUpdateOrbit={() => {}}
        onSetPopulation={async () => {}}
        onAuthorInlineNode={onAuthorInlineNode}
        onAddNodeTerminal={async () => {}}
        onSetNodeTerminalRole={async () => {}}
        onAddNodeEthernet={async () => {}}
        onRemove={() => {}}
        onOpenRule={() => {}}
        onConnect={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Node/ }));
    fireEvent.click(screen.getByRole("button", { name: "Author inline node" }));
    await waitFor(() => expect(onAuthorInlineNode).toHaveBeenCalledTimes(1));
    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("BodySelect failure contract + node-id collision", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ generation: "g1", items: [] }) })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("BodySelect always includes the current value — the field never blanks an existing body", () => {
    render(
      <BodySelect
        label="on body"
        ariaLabel="Body"
        value="catalog:bodies/luna.yaml"
        onChange={() => {}}
        bodies={{ entries: [], error: null, refresh: () => Promise.resolve() }}
      />,
    );
    // Even with an empty catalog, the current body is selected and selectable.
    expect((screen.getByLabelText("Body") as HTMLSelectElement).value).toBe("catalog:bodies/luna.yaml");
  });

  it("BodySelect on catalog error shows the verbatim message and a retry that refreshes", () => {
    const refresh = vi.fn(() => Promise.resolve());
    render(
      <BodySelect
        label="on body"
        ariaLabel="Body"
        value="catalog:bodies/earth.yaml"
        onChange={() => {}}
        bodies={{ entries: [], error: "bodies fetch failed", refresh }}
      />,
    );
    expect(screen.getByText(/bodies fetch failed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    expect(refresh).toHaveBeenCalled();
  });

  it("adding a node delegates installation and identifier seeding to VS-API", async () => {
    const base = draftSite();
    // A gap at gw2 (as a delete-then-render would leave): length+1 would re-mint gw3.
    const site = { ...base, nodes: [base.nodes[0]!, { ...base.nodes[0]!, node_id: "gw3" }] };
    const onAddNode = vi.fn(async () => {});
    const onUpdate = vi.fn();
    render(
      <SiteEditor
        authoring={AUTHORING_FACTS}
        site={site}
        onSetNodeModel={async () => {}}
        onAddNode={onAddNode}
        onUpdate={onUpdate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "+ add node" }));
    await waitFor(() => expect(onAddNode).toHaveBeenCalledTimes(1));
    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("BodySelect lists a catalog body exactly once when the value is already in the catalog", () => {
    const entry = (ref: string, displayName: string): CatalogDocumentSummary =>
      catalogSummary(ref, "bodies", displayName);
    render(
      <BodySelect
        label="on body"
        ariaLabel="Body"
        value="catalog:bodies/earth.yaml"
        onChange={() => {}}
        bodies={{
          entries: [entry("catalog:bodies/earth.yaml", "Earth"), entry("catalog:bodies/luna.yaml", "Luna")],
          error: null,
          refresh: () => Promise.resolve(),
        }}
      />,
    );
    // Earth is both the value AND in the catalog → one option, never a prepend duplicate.
    expect(screen.getAllByRole("option", { name: "Earth" })).toHaveLength(1);
    expect((screen.getByLabelText("Body") as HTMLSelectElement).value).toBe("catalog:bodies/earth.yaml");
  });

  it("submits typed site intent without allocating addresses in the browser", async () => {
    const draft = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
    const onMintSites = vi.fn(async () => {});
    const onUpdate = vi.fn();
    render(
      <GroundEditor
        authoring={AUTHORING_FACTS}
        draft={draft}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
        schedulingPresets={[]}
        selectedSchedulingPreset={null}
        memberSchedulingPreset={() => null}
        onSchedulingPreset={async () => {}}
        onMintSites={onMintSites}
        onAddSiteReference={async () => {}}
        onSetStampNodeModel={async () => {}}
        onSetSiteNodeModel={async () => {}}
        onAddSiteNode={async () => {}}
        onUpdate={onUpdate}
        onRemove={() => {}}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/paste sites, one per line/i), {
      target: { value: "Denver, 39.7, -104.9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "+ mint pasted sites" }));
    await waitFor(() => expect(onMintSites).toHaveBeenCalledTimes(1));
    expect(onMintSites).toHaveBeenCalledWith([
      { name: "Denver", lat_deg: 39.7, lon_deg: -104.9 },
    ]);
    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("editor kit behavior", () => {
  afterEach(cleanup);

  it.each([null, ""])(
    "SelectField shows an explicit empty placeholder for %s state",
    (value) => {
      render(
        <SelectField
          label="backend choice"
          value={value}
          onChange={() => {}}
          options={[{ value: "available", label: "Available" }]}
        />,
      );

      const select = screen.getByRole("combobox", { name: "backend choice" });
      const placeholder = screen.getByRole("option", { name: "Select backend choice" });
      const available = screen.getByRole("option", { name: "Available" });
      expect((select as HTMLSelectElement).value).toBe("");
      expect((placeholder as HTMLOptionElement).disabled).toBe(true);
      expect((placeholder as HTMLOptionElement).selected).toBe(true);
      expect((available as HTMLOptionElement).selected).toBe(false);
    },
  );

  it("SelectField preserves a caller-owned empty option", () => {
    render(
      <SelectField
        label="pointing"
        value={null}
        onChange={() => {}}
        options={[
          { value: "", label: "none" },
          { value: "available", label: "Available" },
        ]}
      />,
    );

    expect(screen.queryByRole("option", { name: "Select pointing" })).toBeNull();
    expect((screen.getByRole("option", { name: "none" }) as HTMLOptionElement).selected).toBe(
      true,
    );
  });

  it("EditorName focuses and selects on create", () => {
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

  it("EditorCard closed reads as spec (summary), open shows the body", () => {
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

describe("editor state is keyed by object identity", () => {
  beforeEach(() => {
    // The catalog fetches behind useBuilderCatalog are irrelevant here — the
    // catalog endpoint returns a bare array (refreshCatalogFamily casts the
    // response to generated catalog summaries), so the stub must too.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ generation: "g1", items: [] }),
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
      authoring: AUTHORING_FACTS,
      workspace: newWorkspace("t"),
      onOpenRule: () => {},
      onConnect: () => {},
      schedulingPresets: [],
      selectedSchedulingPreset: null,
      memberSchedulingPreset: () => null,
      onSchedulingPreset: async () => {},
      onMintSites: async () => {},
      onAddSiteReference: async () => {},
      onSetStampNodeModel: async () => {},
      onSetSiteNodeModel: async () => {},
      onAddSiteNode: async () => {},
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

describe("buffered windows commit through the apply row", () => {
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

  // a window whose applied object moved underneath a dirty working copy
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
    const node = mutableClone(world.nodes.find((n) => n.segment_id === "gnd")!);
    node.terminal_inventory[0]!.min_elevation_deg = 10;
    node.terminal_inventory.push({
      ...node.terminal_inventory[0]!,
      terminal_id: "access_1",
      min_elevation_deg: 30,
    });
    expect(accessBeamElevationDeg(node)).toBe(30);
  });

  it("an access terminal with no declared floor serves to the horizon", () => {
    const node = mutableClone(world.nodes.find((n) => n.segment_id === "shell")!);
    for (const block of node.terminal_inventory) block.min_elevation_deg = null;
    expect(accessBeamElevationDeg(node)).toBe(0);
  });

  it("no access terminal means no beam, not an invented one", () => {
    const node = mutableClone(world.nodes.find((n) => n.segment_id === "shell")!);
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

describe("deferred-clamp number contract (local string draft)", () => {
  afterEach(cleanup);

  const numberInput = () => document.querySelector('input[type="number"]') as HTMLInputElement;

  it("NumberField: empty commits nothing (never 0) and blur re-syncs to the value", () => {
    const seen: number[] = [];
    render(<NumberField label="planes" value={6} min={1} integer onChange={(v) => seen.push(v)} />);
    const box = numberInput();
    fireEvent.change(box, { target: { value: "" } });
    expect(seen).toEqual([]); // empty is not a commit — and not 0
    expect(box.value).toBe(""); // the draft shows exactly what was typed
    fireEvent.blur(box);
    expect(box.value).toBe("6"); // re-sync to the committed value
  });

  it("NumberField: a below-min figure types but does not commit; blur restores", () => {
    const seen: number[] = [];
    render(<NumberField label="planes" value={6} min={4} integer onChange={(v) => seen.push(v)} />);
    const box = numberInput();
    fireEvent.change(box, { target: { value: "2" } });
    expect(seen).toEqual([]); // 2 < min 4 → no commit
    expect(box.value).toBe("2"); // but it is visible while typing
    fireEvent.blur(box);
    expect(box.value).toBe("6"); // never auto-commits the min
  });

  it("NumberField: a valid in-range value commits, rounded for integer fields", () => {
    const seen: number[] = [];
    render(<NumberField label="planes" value={6} min={1} integer onChange={(v) => seen.push(v)} />);
    fireEvent.change(numberInput(), { target: { value: "8.6" } });
    expect(seen).toEqual([9]);
  });

  it("NumberField: negatives are typeable and commit when a negative min allows them", () => {
    const seen: number[] = [];
    render(<NumberField label="offset" value={0} min={-90} onChange={(v) => seen.push(v)} />);
    fireEvent.change(numberInput(), { target: { value: "-45" } });
    expect(seen).toEqual([-45]);
  });

  it("SliderField box: min is the floor — below-min types but does not commit", () => {
    const seen: number[] = [];
    render(
      <SliderField label="altitude" value={550} min={150} max={40000} onChange={(v) => seen.push(v)} />,
    );
    const box = numberInput();
    fireEvent.change(box, { target: { value: "100" } });
    expect(seen).toEqual([]); // below the floor → no commit
    expect(box.value).toBe("100"); // typeable
    fireEvent.blur(box);
    expect(box.value).toBe("550"); // re-sync
  });

  it("NullableNumberField: below-min commits nothing, yet empty still means null", () => {
    let value: number | null = 25;
    render(
      <NullableNumberField
        label="min elevation"
        placeholder="none"
        value={value}
        min={10}
        onChange={(v) => {
          value = v;
        }}
      />,
    );
    const box = screen.getByPlaceholderText("none");
    fireEvent.change(box, { target: { value: "5" } });
    expect(value).toBe(25); // 5 < min 10 → unchanged
    fireEvent.change(box, { target: { value: "" } });
    expect(value).toBeNull(); // empty → null contract survives the draft rewrite
  });
});

describe("the anatomy guide answers what-next in any order", () => {
  afterEach(cleanup);
  const guideProps = (
    ws: ReturnType<typeof newWorkspace>,
    resolvedSiteCount: number | null = null,
    sessionNameIsPlaceholder = false,
  ) => ({
    workspace: ws,
    sessionNameIsPlaceholder,
    saved: null,
    deployed: false,
    resolvedSiteCount,
    onAddConstellation: () => {},
    onAddGround: () => {},
    onAddDomain: () => {},
    onOpenSession: () => {},
    onOpenSegment: () => {},
  });

  it("every anatomy row is always on screen, pending or done", () => {
    render(<BuildGuide {...guideProps(newWorkspace("untitled-session-a1b2"), null, true)} />);
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
    const ws = newWorkspace("untitled-session-a1b2");
    render(
      <BuildGuide
        {...guideProps(ws, null, true)}
        onAddConstellation={() => calls.push("space")}
      />,
    );
    fireEvent.click(screen.getByText("Space segments"));
    expect(calls).toEqual(["space"]);
    // No segments yet: comms intent explains its precondition instead of acting.
    expect(screen.getByText("needs two segments first")).toBeTruthy();
    expect(screen.getByText("add links first")).toBeTruthy();
  });

  it("uses the backend placeholder fact instead of guessing from the generated name", () => {
    render(<BuildGuide {...guideProps(newWorkspace("backend-generated-name"), null, true)} />);

    expect(screen.getByText("name it — real time unless you say otherwise")).toBeTruthy();
    expect(screen.queryByText("backend-generated-name")).toBeNull();
  });

  it("keeps an empty session name incomplete even when it is not marked as a placeholder", () => {
    render(<BuildGuide {...guideProps(newWorkspace(""), null, false)} />);

    expect(screen.getByText("name it — real time unless you say otherwise")).toBeTruthy();
  });

  it("done rows show counts, not health claims", () => {
    const ws = newWorkspace("named-session");
    ws.space.push(newDraftConstellation("nodalarc:nodes/space/x.yaml"));
    render(<BuildGuide {...guideProps(ws)} />);
    expect(screen.getByText("1 segment · add more")).toBeTruthy();
    expect(screen.getByText("named-session")).toBeTruthy();
  });

  it("the resolved distinct-namespace site count is shown as-is", () => {
    // A single two-node site resolves to ONE namespace → count 1, not 2.
    render(<BuildGuide {...guideProps(newWorkspace("named"), 1)} />);
    expect(screen.getByText("1 site · add more")).toBeTruthy();
  });

  it("a multi-site resolved count is shown, not the draft node count", () => {
    render(<BuildGuide {...guideProps(newWorkspace("named"), 3)} />);
    expect(screen.getByText("3 sites · add more")).toBeTruthy();
  });

  it("before the world resolves, the count falls back to the draft, flagged unresolved", () => {
    const ws = newWorkspace("named");
    const ground = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
    ground.members = [
      testGroundMember(ground, "Denver", 39.7, -104.9),
      testGroundMember(ground, "Ames", 42, -93, 1),
    ];
    ws.ground.push(ground);
    // resolvedSiteCount null → the draft member count (2) with the qualifier.
    render(<BuildGuide {...guideProps(ws, null)} />);
    expect(screen.getByText("2 sites (unresolved) · add more")).toBeTruthy();
  });
});

describe("ambiguous artifact wording stays out of the Builder surface", () => {
  // Canonical YAML, saved catalog revisions, and dependency digests are distinct
  // facts. A generic artifact label would collapse those identities.
  const ARTICLE = "the";
  const LEAK = new RegExp(`\\b${ARTICLE} artifact\\b`, "i");
  const leakOffenders = (): string[] => {
    const offenders: string[] = [];
    const scan = (dir: string, prefix: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory()) {
          if (entry.name === "__tests__") scan(join(dir, entry.name), `${prefix}${entry.name}/`);
          continue;
        }
        if (!entry.name.endsWith(".ts") && !entry.name.endsWith(".tsx")) continue;
        readFileSync(join(dir, entry.name), "utf-8")
          .split("\n")
          .forEach((line, i) => {
            if (LEAK.test(line)) offenders.push(`${prefix}${entry.name}:${i + 1} ${line.trim()}`);
          });
      }
    };
    scan(BUILDER_DIR, "");
    return offenders;
  };

  it("no builder file names the authoring document an artifact (leading article)", () => {
    expect(leakOffenders(), "document-scoped artifact-language leak").toEqual([]);
  });
});

describe("generated visual workspace authority", () => {
  it("has no handwritten Workspace or Draft interface tree", () => {
    const source = readFileSync(join(BUILDER_DIR, "workspace.ts"), "utf-8");
    expect(source).toContain("MaterializedMutable<BuilderVisualWorkspace>");
    expect(source).not.toMatch(/export interface (?:Workspace|Draft\w*)\b/);
  });

  it("has no field-by-field visual workspace converter", () => {
    const source = readFileSync(join(BUILDER_DIR, "visualWorkspace.ts"), "utf-8");
    expect(source).not.toContain("visualWorkspaceFromWorkspace");
    expect(source).not.toContain(".map(");
    expect(source).not.toContain("session_name: workspace.name");
    expect(source).not.toContain("source_ref: space.ref");
    expect(source).not.toContain("site_set_ref: ground.ref");
  });
});

describe("the closed vocabularies have one owner", () => {
  // Non-recursive, like the raw-control scan: production builder files are flat (only
  // __tests__ is a subdirectory). A future production subdir would need this
  // widened.
  const vocabularyOffenders = (pattern: RegExp): string[] => {
    const offenders: string[] = [];
    for (const entry of readdirSync(BUILDER_DIR)) {
      if (!entry.endsWith(".ts") && !entry.endsWith(".tsx")) continue;
      // builderTypes.ts holds render-world unions as TYPES (a | b | c), not an
      // offered option list. Generated visual DTOs and backend authoring facts
      // own the selectable vocabularies.
      if (entry === "builderTypes.ts") continue;
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
      "role vocabulary re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
  });

  it("no builder source file re-lists the link media as an array literal", () => {
    expect(
      vocabularyOffenders(/\[\s*"(rf|optical)"\s*,\s*"(rf|optical)"/),
      "media vocabulary re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
  });

  // Option-object arrays ([{value:"isis"},…]) require a separate check because
  // which the bare-string patterns above cannot see. Each vocabulary is checked
  // in both forms: an option-object pair (value:"tok"…value:"tok") and a
  // bare-string array ([ "tok", "tok" ]) — so neither shape can re-list it. The
  // Generated unions and backend metadata are the primary contract; this scan
  // catches browser-owned option inventories.
  it("no builder source file re-lists the routing protocols", () => {
    const tok = "isis|ospf|bgp|static";
    expect(
      vocabularyOffenders(new RegExp(`value:\\s*"(${tok})"[\\s\\S]{0,80}value:\\s*"(${tok})"`)),
      "protocol vocabulary re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
    expect(
      vocabularyOffenders(new RegExp(`\\[\\s*"(${tok})"\\s*,\\s*"(${tok})"`)),
      "protocol array re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
  });

  it("no builder source file re-lists the boundary adapters", () => {
    const tok = "static_ip|bgp|dtn_bundle";
    expect(
      vocabularyOffenders(new RegExp(`value:\\s*"(${tok})"[\\s\\S]{0,80}value:\\s*"(${tok})"`)),
      "adapter vocabulary re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
    expect(
      vocabularyOffenders(new RegExp(`\\[\\s*"(${tok})"\\s*,\\s*"(${tok})"`)),
      "adapter array re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
  });

  it("no builder source file re-lists the forwarding modes", () => {
    const tok = "routed|host|bridge|control_only";
    expect(
      vocabularyOffenders(new RegExp(`value:\\s*"(${tok})"[\\s\\S]{0,80}value:\\s*"(${tok})"`)),
      "forwarding vocabulary re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
    expect(
      vocabularyOffenders(new RegExp(`\\[\\s*"(${tok})"\\s*,\\s*"(${tok})"`)),
      "forwarding array re-listed outside generated contracts and backend metadata",
    ).toEqual([]);
  });
});

describe("a save is never a dead end", () => {
  it("every library save announces the asset through the reveal store", async () => {
    const { requestLibraryReveal, useLibraryReveal } = await import("../useBuilderWorld");
    let latest: unknown = null;
    function Probe() {
      latest = useLibraryReveal();
      return null;
    }
    render(<Probe />);
    const entry = catalogSummary(
      "user:terminals/test-radio.yaml",
      "terminals",
      "Test radio",
    );
    act(() => requestLibraryReveal(entry));
    expect((latest as { entry: typeof entry }).entry.ref).toBe(
      "user:terminals/test-radio.yaml",
    );
  });

  it("the toolbar owns the session verbs as icon buttons; the rail owns none", () => {
    const source = readFileSync(join(BUILDER_DIR, "BuilderView.tsx"), "utf-8");
    // the slice anchors must EXIST and be ORDERED (toolbar < outline <
    // canvas). Without this a renamed anchor makes indexOf return -1, the slice
    // is empty/backwards, and the rail `.not.toContain` below passes vacuously —
    // a false green. Assert the anchors so a rename breaks this test loudly.
    const iToolbar = source.indexOf('className="builder-toolbar"');
    const iOutline = source.indexOf('className="builder-outline"');
    const iCanvas = source.indexOf('className="builder-canvas"');
    expect(iToolbar, "toolbar anchor present").toBeGreaterThanOrEqual(0);
    expect(iOutline, "outline anchor after toolbar").toBeGreaterThan(iToolbar);
    expect(iCanvas, "canvas anchor after outline").toBeGreaterThan(iOutline);

    const toolbar = source.slice(iToolbar, iOutline);
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
    const rail = source.slice(iOutline, iCanvas);
    expect(rail.length, "the rail slice is non-empty").toBeGreaterThan(0);
    for (const verb of ["Save session", "Deploy to cluster", "Library…"]) {
      expect(rail, `rail must not carry "${verb}" as a control`).not.toContain(`>${verb}<`);
    }
  });

  it("each reveal consumer role claims a nonce once, across remounts", async () => {
    const { claimLibraryReveal } = await import("../useBuilderWorld");
    const entry = catalogSummary(
      "user:terminals/claim-probe.yaml",
      "terminals",
      "Claim probe",
    );
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

describe("deploy gate: saved backend verdict and exact digest truth", () => {
  const savedVerdict = {
    allowed: true,
    session_ref: "user:sessions/x.yaml",
    session_revision: "revision-x",
    digests: {
      document: "doc-x",
      dependency: "dep-x",
    },
    blockers: [],
  };

  it("deploys only when the saved revision matches the settled compile", () => {
    expect(
      canDeploy({
        savedVerdict,
        settledDocumentDigest: "doc-x",
        settledDependencyDigest: "dep-x",
        dirtyWindowCount: 0,
      }),
    ).toEqual({ ok: true, reason: null });
  });

  it("refuses without a save", () => {
    const gate = canDeploy({
      savedVerdict: null,
      settledDocumentDigest: "doc-x",
      settledDependencyDigest: "dep-x",
      dirtyWindowCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/save the session first/);
  });

  it("fails closed when no compile has settled (cleared or refused)", () => {
    const gate = canDeploy({
      savedVerdict,
      settledDocumentDigest: null,
      settledDependencyDigest: null,
      dirtyWindowCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/must compile/);
  });

  it("refuses when the saved backend verdict blocks deployment", () => {
    const gate = canDeploy({
      savedVerdict: {
        ...savedVerdict,
        allowed: false,
        blockers: [
          {
            code: "readiness.no_satellites",
            stage: "readiness",
            severity: "error",
            message: "no satellites — the session cannot start on the cluster",
            blocks: ["deploy"],
          },
        ],
      },
      settledDocumentDigest: "doc-x",
      settledDependencyDigest: "dep-x",
      dirtyWindowCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/no satellites/);
  });

  it("refuses while windows hold unapplied edits", () => {
    const gate = canDeploy({
      savedVerdict,
      settledDocumentDigest: "doc-x",
      settledDependencyDigest: "dep-x",
      dirtyWindowCount: 2,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/2 windows with unapplied edits/);
  });

  it("names the staleness when the saved copy is behind the edits", () => {
    const gate = canDeploy({
      savedVerdict,
      settledDocumentDigest: "doc-y",
      settledDependencyDigest: "dep-x",
      dirtyWindowCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/behind your edits/);
  });
});

describe("commitWorkspace: one atomic adoption, one undo entry", () => {
  it("undo after commitWorkspace returns exactly to the pre-commit state", () => {
    const { result } = renderHook(() => useWorkspace());
    act(() => result.current.openWorkspace(newWorkspace("alpha")));
    const before = result.current.workspace;
    expect(before?.session_name).toBe("alpha");
    act(() =>
      result.current.commitWorkspace(
        { ...before!, session_name: "beta" },
        "test-adoption",
      ),
    );
    expect(result.current.workspace?.session_name).toBe("beta");
    act(() => result.current.undo());
    expect(result.current.workspace?.session_name).toBe("alpha");
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
      session: {
        draft: { session_name: "picked" },
        opened: { session_name: ws.session_name },
        dirty: true,
      },
    });
    expect(renamed.session_name).toBe("picked");
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
        draft: { session_name: "typed" },
        opened: { session_name: ws.session_name },
        dirty: true,
      },
    };
    expect(staleBufferKeys(ws, buffers)).toEqual([]);
    expect(
      staleBufferKeys({ ...ws, session_name: "renamed-by-undo" }, buffers),
    ).toEqual([
      "session",
    ]);
  });

  // the reconciliation pass decides prune/drop through the same two
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

  // bulk apply-and-save: the confirm flow DECLINES a stale window by leaving
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

describe("open picker: namespace is the catalog's word, not a path sniff", () => {
  it("OpenSessionPicker groups by typed namespace and never sniffs a path", () => {
    const source = readFileSync(join(BUILDER_DIR, "OpenSessionPicker.tsx"), "utf-8");
    // The server names each entry's root tier; the client does not infer it
    // from the server's directory layout.
    expect(source).not.toContain("generated-sessions");
    expect(source).toContain('entry.namespace === "user"');
    expect(source).toContain('entry.namespace === "nodalarc"');
    // The tiers speak the library's own vocabulary.
    expect(source).toContain('group("★ yours"');
    expect(source).toContain('group("nodalarc library"');
  });

  it("production open/save paths have no retired filesystem or local-parser authority", () => {
    const view = readFileSync(join(BUILDER_DIR, "BuilderView.tsx"), "utf-8");
    const world = readFileSync(join(BUILDER_DIR, "useBuilderWorld.ts"), "utf-8");
    expect(view).not.toContain("useSessionImport");
    expect(view).not.toContain("workspaceFromSessionDocument");
    expect(world).not.toContain("/api/v1/sessions");
    expect(world).not.toContain("/builder/resolve-world");
    expect(world).toContain("getCatalogDocument");
    expect(world).toContain("compileVisualDraft");
    expect(world).not.toContain("compileBuilderDraft");
  });
});

describe("workspaceForSave: the dialog name never silently undoes a rename", () => {
  it("apply-and-save keeps a dirty Session-window rename when the field is untouched", () => {
    const ws = newWorkspace("old-name");
    const buffers = {
      session: {
        draft: { session_name: "renamed-in-session" },
        opened: { session_name: "old-name" },
        dirty: true,
      },
    };
    const next = workspaceForSave(ws, buffers, {
      applyAll: true,
      dialogName: "old-name",
      nameTouched: false,
    });
    expect(next.session_name).toBe("renamed-in-session");
  });

  it("a name the user actually typed in the dialog wins over the overlays", () => {
    const ws = newWorkspace("old-name");
    const buffers = {
      session: {
        draft: { session_name: "renamed-in-session" },
        opened: { session_name: "old-name" },
        dirty: true,
      },
    };
    const next = workspaceForSave(ws, buffers, {
      applyAll: true,
      dialogName: "typed name",
      nameTouched: true,
    });
    expect(next.session_name).toBe("typed name");
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

  it("a touched empty name is preserved for backend validation", () => {
    const ws = newWorkspace("kept");
    const next = workspaceForSave(ws, {}, {
      applyAll: false,
      dialogName: "",
      nameTouched: true,
    });
    expect(next.session_name).toBe("");
  });
});
