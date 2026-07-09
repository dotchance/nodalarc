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
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BodySelect,
  EditorApplyRow,
  EditorCard,
  EditorName,
  NumberField,
  NullableNumberField,
  SliderField,
} from "../editorKit";
import { BuildGuide } from "../BuildGuide";
import { GroundEditor } from "../GroundEditor";
import { SiteEditor } from "../SiteEditor";
import { ConstellationEditor } from "../ConstellationEditor";
import {
  accessBeamElevationDeg,
  capabilitiesBySegment,
  connectSegments,
  deriveLinkPhysics,
  groundMaskSeedNote,
  rederiveRule,
  SEEDED_GROUND_MASK_NOTE,
} from "../linkPhysics";
import {
  identifier,
  LINK_MEDIA,
  mintSiteMembers,
  newDraftConstellation,
  newDraftGroundSet,
  newDraftSiteObject,
  newWorkspace,
  nextMintIndex,
  parseSiteLines,
  siteSetWrapperFromDraft,
  stampLanPrefix,
} from "../workspace";
import type { BuilderCatalogEntry } from "../builderTypes";
import { canDeploy, resetCatalogStores } from "../useBuilderWorld";
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

describe("IG-5: builder surfaces compose the kit, never raw controls", () => {
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

describe("IG-5: card anatomy lives only in the kit (EditorCard)", () => {
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

describe("EditorCard adoption smoke: migrated editors render and their cards behave", () => {
  beforeEach(() => {
    // The editors read catalogs on mount; the endpoint returns a bare array.
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => [] })));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("SiteEditor: a multi-node site card carries a working Remove (the actions slot)", () => {
    const base = newDraftSiteObject("nodalarc:nodes/ground/gw.yaml", {});
    const site = { ...base, nodes: [base.nodes[0]!, { ...base.nodes[0]!, node_id: "gw2" }] };
    let updated = site;
    render(
      <SiteEditor
        site={site}
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
        draft={newDraftConstellation("nodalarc:nodes/space/leo.yaml")}
        workspace={newWorkspace("t")}
        onUpdate={() => {}}
        onUpdateOrbit={() => {}}
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
});

describe("P7e: BodySelect failure contract + node-id collision (N27)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => [] })));
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

  it("N27: adding a node fills the first free gw slot (no collision after a delete)", () => {
    const base = newDraftSiteObject("nodalarc:nodes/ground/gw.yaml", {});
    // A gap at gw2 (as a delete-then-render would leave): length+1 would re-mint gw3.
    const site = { ...base, nodes: [base.nodes[0]!, { ...base.nodes[0]!, node_id: "gw3" }] };
    let added = site;
    render(
      <SiteEditor
        site={site}
        onUpdate={(update) => {
          added = update(site);
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "+ add node" }));
    expect(added.nodes.map((n) => n.node_id)).toEqual(["gw1", "gw3", "gw2"]);
  });

  it("BodySelect lists a catalog body exactly once when the value is already in the catalog", () => {
    const entry = (ref: string, display_name: string): BuilderCatalogEntry =>
      ({
        ref,
        family: "bodies",
        id: ref.split("/").pop() ?? null,
        display_name,
        notes: null,
        summary: null,
        error: null,
      }) as BuilderCatalogEntry;
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

  it("N29: the next-minted-site preview reflects nextMintIndex, not a literal 0", () => {
    const draft = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
    // One already-minted member advances the next index past 0.
    draft.members = mintSiteMembers(draft, parseSiteLines("Denver, 39.7, -104.9").rows);
    expect(nextMintIndex(draft)).toBeGreaterThan(0);
    render(
      <GroundEditor
        draft={draft}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
        onUpdate={() => {}}
        onRemove={() => {}}
      />,
    );
    // Open the New-site stamp card (Sites is open by default) to reveal the preview.
    fireEvent.click(screen.getByRole("button", { name: /New-site stamp/ }));
    const preview = [...document.querySelectorAll(".builder-site-derived")].find((el) =>
      el.textContent?.includes("next minted site:"),
    );
    // The preview shows the NEXT mint's address (index nextMintIndex) — a revert to
    // the literal 0 would show a different, colliding address and fail this.
    expect(preview?.textContent).toContain(stampLanPrefix(draft.stamp, nextMintIndex(draft)));
  });
});

describe("P7g: save-to-library convergence wiring (D7)", () => {
  beforeEach(() => {
    resetCatalogStores();
    // The save endpoint returns a family-specific ref; every other catalog read
    // (the post-save family refresh included) returns an empty list.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: { body?: string }) => {
        if (String(url).includes("/builder/catalog/save")) {
          // useLibrarySave posts the family in the body — echo a ref under it.
          const family = init?.body ? JSON.parse(init.body).family : "sites";
          return {
            ok: true,
            status: 200,
            json: async () => ({ ref: `user:${family}/x.yaml`, family }),
          };
        }
        return { ok: true, json: async () => [] };
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  it("saving the whole set reports (ref, wrapper) to onSaved — the window-annotation source", async () => {
    const draft = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
    draft.members = mintSiteMembers(draft, parseSiteLines("Denver, 39.7, -104.9").rows);
    const onSaved = vi.fn();
    render(
      <GroundEditor
        draft={draft}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
        onUpdate={() => {}}
        onRemove={() => {}}
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    // The exact wrapper the save posted — so the window stores the same shape the
    // close-time comparator re-serializes the applied set into.
    expect(onSaved.mock.calls[0]).toEqual([
      "user:site-sets/x.yaml",
      siteSetWrapperFromDraft(draft),
    ]);
  });

  it("saving an authored member flips it to a ref in place, keeping member_id + override", async () => {
    const draft = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
    draft.members = mintSiteMembers(draft, parseSiteLines("Denver, 39.7, -104.9").rows);
    const member = draft.members[0]!;
    member.scheduling_override = "geo-longest-pass"; // a session-owned block to preserve
    let updated = draft;
    render(
      <GroundEditor
        draft={draft}
        workspace={newWorkspace("t")}
        onOpenRule={() => {}}
        onConnect={() => {}}
        onUpdate={(update) => {
          updated = update(updated);
        }}
        onRemove={() => {}}
      />,
    );
    // Open the member's inline editor, then save it to the sites library.
    fireEvent.click(screen.getByRole("button", { name: `Edit ${member.label}` }));
    const embedded = screen.getByTestId("builder-site-editor");
    fireEvent.click(within(embedded).getByRole("button", { name: "Save to library" }));
    await waitFor(() => expect(updated.members[0]!.kind).toBe("ref"));
    const flipped = updated.members[0]!;
    expect(flipped.member_id).toBe(member.member_id); // never minted fresh
    expect(flipped.ref).toBe("user:sites/x.yaml");
    expect(flipped.site).toBeNull();
    expect(flipped.scheduling_override).toBe("geo-longest-pass"); // session-owned, preserved
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

  it("M11: a floorless ground mask is a SEEDED default, said plainly in both carriers", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId, null); // no terminal declares a floor
    const rule = connectSegments(workspace, world, groundId, spaceId);
    // The seed value is applied via the default, not derived from a terminal.
    expect(rule.a.min_elevation_deg).toBe(25);
    // Carrier 1 — the editor's seed note (the connect-seed path).
    const caps = capabilitiesBySegment(world);
    expect(
      groundMaskSeedNote(caps.get(groundId), {
        role: "access",
        kind: "ground",
        min_elevation_deg: 25,
      }),
    ).toBe("no terminal declares an elevation floor — seeded default 25°");
    expect(SEEDED_GROUND_MASK_NOTE).toBe("no terminal declares an elevation floor — seeded default 25°");
    // Carrier 2 — the re-derive notice; a seed reads as a seed, never "derived".
    const { notice } = rederiveRule(workspace, world, rule, "b", spaceId);
    expect(notice).toContain("no terminal declares an elevation floor — seeded default 25°");
    expect(notice).not.toMatch(/· 25° mask/);
  });

  it("M11: a DECLARED floor is not seeded — the seed note stays off, the notice shows the value", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId, 25); // the ground terminal declares 25°
    const rule = connectSegments(workspace, world, groundId, spaceId);
    const caps = capabilitiesBySegment(world);
    expect(
      groundMaskSeedNote(caps.get(groundId), {
        role: "access",
        kind: "ground",
        min_elevation_deg: 25,
      }),
    ).toBeNull();
    const { notice } = rederiveRule(workspace, world, rule, "b", spaceId);
    expect(notice).toContain("· 25° mask");
    expect(notice).not.toContain("seeded default");
  });

  it("N32: the unformable fallback medium comes from the owned vocabulary, not literals", () => {
    // Empty capabilities -> nothing is formable, so derivation hits the
    // fallback; its medium must be drawn from LINK_MEDIA (the owned set), the
    // role-appropriate default, never a re-listed string.
    const empty = capabilitiesBySegment(null);
    const access = deriveLinkPhysics(
      empty,
      { segment_id: "g", kind: "ground" },
      { segment_id: "s", kind: "space" },
    );
    expect(access.formable).toBe(false);
    expect(LINK_MEDIA).toContain(access.medium);
    expect(access.medium).toBe("rf"); // access -> radio, the highest-rank default
    const fabric = deriveLinkPhysics(
      empty,
      { segment_id: "s", kind: "space" },
      { segment_id: "s", kind: "space" },
    );
    expect(fabric.formable).toBe(false);
    expect(fabric.medium).toBe("optical"); // fabric -> optical, the lowest-rank default
  });

  // M22: rederiveRule's PATCH (not just its notice) — the physics it writes
  // when an endpoint moves, and the loud refusal when it cannot re-derive.
  it("M22: a role flip to isl clears the now-irrelevant elevation mask", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId);
    const access = connectSegments(workspace, world, groundId, spaceId); // ground↔space access
    expect(access.a.min_elevation_deg).toBe(25); // ground side carries the mask
    // Move the ground endpoint to the space segment → space↔space isl.
    const { patch } = rederiveRule(workspace, world, access, "a", spaceId);
    expect(patch.a!.role).toBe("isl");
    expect(patch.a!.min_elevation_deg).toBeNull(); // the mask is cleared, not carried
    expect(patch.b!.min_elevation_deg).toBeNull();
  });

  it("M22: the ground side of a re-derived access rule gets the elevation mask", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId);
    const isl = connectSegments(workspace, world, spaceId, spaceId); // space↔space isl, no mask
    expect(isl.a.min_elevation_deg).toBeNull();
    // Move endpoint a to the ground segment → access; the ground side gets the mask.
    const { patch } = rederiveRule(workspace, world, isl, "a", groundId);
    expect(patch.a!.role).toBe("access");
    expect(patch.a!.min_elevation_deg).toBe(25); // ground side masked
    expect(patch.b!.min_elevation_deg).toBeNull(); // space side floorless
  });

  it("M22: an unformable re-derivation carries the warning, never silent physics", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId);
    const rule = connectSegments(workspace, world, groundId, spaceId);
    // A null world → no capabilities → neither side has matching terminals.
    const { notice } = rederiveRule(workspace, null, rule, "b", spaceId);
    expect(notice).toContain("WARNING: neither side has matching terminals");
  });

  it("M22: re-deriving onto an unplaced segment invents no physics", () => {
    const { workspace, groundId, spaceId } = connectWorkspace();
    const world = tinyWorld(groundId, spaceId);
    const rule = connectSegments(workspace, world, groundId, spaceId);
    const { patch, notice } = rederiveRule(workspace, world, rule, "b", "not-a-placed-segment");
    expect(notice).toBe("endpoint changed — pick a placed segment to re-derive physics");
    expect(patch.b!.segment_id).toBe("not-a-placed-segment"); // the id moves
    expect(patch.a).toBeUndefined(); // only the changed side
    expect(patch.topology_mode).toBeUndefined(); // and no fabricated geometry
  });
});

describe("IG-4: editor state is keyed by object identity", () => {
  beforeEach(() => {
    // The catalog fetches behind useBuilderCatalog are irrelevant here — the
    // catalog endpoint returns a bare array (refreshCatalogFamily casts the
    // response to BuilderCatalogEntry[]), so the stub must too.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => [],
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

describe("IG-15: the anatomy guide answers what-next in any order", () => {
  afterEach(cleanup);
  const guideProps = (ws: ReturnType<typeof newWorkspace>, resolvedSiteCount: number | null = null) => ({
    workspace: ws,
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

  it("N52: the resolved distinct-namespace site count is shown as-is", () => {
    // A single two-node site resolves to ONE namespace → count 1, not 2.
    render(<BuildGuide {...guideProps(newWorkspace("named"), 1)} />);
    expect(screen.getByText("1 site · add more")).toBeTruthy();
  });

  it("N52: a multi-site resolved count is shown, not the draft node count", () => {
    render(<BuildGuide {...guideProps(newWorkspace("named"), 3)} />);
    expect(screen.getByText("3 sites · add more")).toBeTruthy();
  });

  it("N52: before the world resolves, the count falls back to the draft, flagged unresolved", () => {
    const ws = newWorkspace("named");
    const ground = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
    ground.members = mintSiteMembers(ground, parseSiteLines("Denver, 39.7, -104.9\nAmes, 42, -93").rows);
    ws.ground.push(ground);
    // resolvedSiteCount null → the draft member count (2) with the qualifier.
    render(<BuildGuide {...guideProps(ws, null)} />);
    expect(screen.getByText("2 sites (unresolved) · add more")).toBeTruthy();
  });
});

describe("N55: \"artifact\" survives only in save-form contexts", () => {
  // After P0a, "artifact" names the flattened SAVE form (artifact_sha256, the
  // save dialog). Naming the authoring PANE/document an artifact with a leading
  // article is a false-state display. Scan every builder file INCLUDING
  // __tests__ for that article+noun phrase; save-form uses ("saved artifact",
  // "settled artifact hash", artifact_sha256) carry a word between and never
  // match. The phrase is built from a variable so this scan never flags itself.
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

describe("IG-16: the closed vocabularies have one owner", () => {
  // Non-recursive, like the IG-5 scan: production builder files are flat (only
  // __tests__ is a subdirectory). A future production subdir would need this
  // widened.
  const vocabularyOffenders = (pattern: RegExp): string[] => {
    const offenders: string[] = [];
    for (const entry of readdirSync(BUILDER_DIR)) {
      if (!entry.endsWith(".ts") && !entry.endsWith(".tsx")) continue;
      if (entry === "workspace.ts") continue; // the owner
      // builderTypes.ts holds the wire-twin unions as TYPES (a | b | c), not an
      // offered option list — exempt alongside the owner.
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
      "role vocabulary re-listed outside workspace.ts",
    ).toEqual([]);
  });

  it("no builder source file re-lists the link media as an array literal", () => {
    expect(
      vocabularyOffenders(/\[\s*"(rf|optical)"\s*,\s*"(rf|optical)"/),
      "media vocabulary re-listed outside workspace.ts",
    ).toEqual([]);
  });

  // N21: the real offenders at HEAD are option-OBJECT arrays ([{value:"isis"},…]),
  // which the bare-string patterns above cannot see. Each vocabulary is checked
  // in BOTH forms — an option-object pair (value:"tok"…value:"tok") and a
  // bare-string array ([ "tok", "tok" ]) — so neither shape can re-list it. The
  // `satisfies Record<union,…>` maps in workspace.ts are the primary compile-time
  // catch; this scan is the backstop.
  it("no builder source file re-lists the routing protocols", () => {
    const tok = "isis|ospf|bgp|static";
    expect(
      vocabularyOffenders(new RegExp(`value:\\s*"(${tok})"[\\s\\S]{0,80}value:\\s*"(${tok})"`)),
      "protocol vocabulary re-listed (option objects) outside workspace.ts",
    ).toEqual([]);
    expect(
      vocabularyOffenders(new RegExp(`\\[\\s*"(${tok})"\\s*,\\s*"(${tok})"`)),
      "protocol vocabulary re-listed (bare array) outside workspace.ts",
    ).toEqual([]);
  });

  it("no builder source file re-lists the boundary adapters", () => {
    const tok = "static_ip|bgp|dtn_bundle";
    expect(
      vocabularyOffenders(new RegExp(`value:\\s*"(${tok})"[\\s\\S]{0,80}value:\\s*"(${tok})"`)),
      "adapter vocabulary re-listed (option objects) outside workspace.ts",
    ).toEqual([]);
    expect(
      vocabularyOffenders(new RegExp(`\\[\\s*"(${tok})"\\s*,\\s*"(${tok})"`)),
      "adapter vocabulary re-listed (bare array) outside workspace.ts",
    ).toEqual([]);
  });

  it("no builder source file re-lists the forwarding modes", () => {
    const tok = "routed|host|bridge|control_only";
    expect(
      vocabularyOffenders(new RegExp(`value:\\s*"(${tok})"[\\s\\S]{0,80}value:\\s*"(${tok})"`)),
      "forwarding vocabulary re-listed (option objects) outside workspace.ts",
    ).toEqual([]);
    expect(
      vocabularyOffenders(new RegExp(`\\[\\s*"(${tok})"\\s*,\\s*"(${tok})"`)),
      "forwarding vocabulary re-listed (bare array) outside workspace.ts",
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
    // N38: the slice anchors must EXIST and be ORDERED (toolbar < outline <
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
  it("OpenSessionPicker groups by entry.source and never sniffs the file path", () => {
    const source = readFileSync(join(BUILDER_DIR, "OpenSessionPicker.tsx"), "utf-8");
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
