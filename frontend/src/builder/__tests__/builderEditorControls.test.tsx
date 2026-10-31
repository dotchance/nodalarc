// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder editor control conformance.
 *
 *  static scan: builder editors compose the editor kit; a raw
 *  <input>/<select>/<textarea> in an editor file is a violation (file
 *  inputs excepted — they are not editing controls). Same enforcement
 *  pattern as the stylesheet token scan.
 *
 *  Kit behavior: number fields never commit an empty or out-of-range
 *  figure as a value; empty means unset.
 */

import { readdirSync, readFileSync } from "node:fs";
import type { ComponentProps } from "react";
import { join } from "node:path";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BodySelect,
  NumberField,
  NullableNumberField,
  SelectField,
} from "../editorKit";
import { BuildGuide } from "../BuildGuide";
import { LinkRuleEditor, LinkRuleWindow } from "../LinkRuleEditor";
import { PendingEditApplyRow } from "../pendingEditKit";
import { RoutingDomainWindow } from "../RoutingEditor";
import { AUTHORING_FACTS } from "./fixtures/authoringFacts";
import { sessionSegments } from "../sessionSegments";
import type {
  BuilderVisualLinkRule,
  BuilderVisualRoutingDomain,
  CatalogDocumentSummary,
  JsonValue,
} from "../generated/builderApi";
import {
  canDeploy,
  draftHoldsUnsavedWork,
  yamlBlocksGraphicalEditing,
} from "../useBuilderWorld";
import { tinyWorld } from "./fixtures/tinyWorld";

const BUILDER_DIR = join(__dirname, "..");

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

describe("editors delegate authoring to VS-API", () => {
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
});

describe("a link or routing window's commit row", () => {
  afterEach(cleanup);

  const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

  it("while the edit cannot be applied, Apply and OK wait and say why, and Cancel stays", () => {
    render(
      <PendingEditApplyRow
        pending
        outcome={null}
        blocked="Apply or canonicalize the session YAML before editing here."
        checking={false}
        onApply={() => Promise.resolve(true)}
        onReapply={() => Promise.resolve(true)}
        onDiscard={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(button("Apply").disabled).toBe(true);
    expect(button("OK").disabled).toBe(true);
    expect(button("OK").title).toBe("Apply or canonicalize the session YAML before editing here.");
    expect(button("Cancel").disabled).toBe(false);
  });
});

describe("the link editor reads terminal capabilities from the world", () => {
  afterEach(cleanup);

  function renderLinkEditor(withWorld: boolean) {
    const placed = sessionSegments({
      segments: [
        { id: "shell", source: "nodalarc:constellations/leo.yaml" },
        { id: "gnd", placement: { from_site_set: "nodalarc:site-sets/gw.yaml" } },
      ],
    });
    const endpoint = (segment_id: string) => ({
      segment_id,
      tag: null,
      role: "access" as const,
      medium: "rf" as const,
      min_elevation_deg: null,
    });
    const rule: BuilderVisualLinkRule = {
      rule_id: "ground-to-space",
      enabled: true,
      a: endpoint("gnd"),
      b: endpoint("shell"),
      topology_mode: null,
      topology_n: null,
      max_range_km: null,
    };
    // The ground segment carries rf access only; the space segment also
    // carries optical isl.
    const world = tinyWorld("gnd", "shell");
    render(
      <LinkRuleEditor
        authoring={AUTHORING_FACTS}
        placed={placed}
        rule={rule}
        segments={withWorld ? world.segments : null}
        allocation={null}
        onRepoint={async () => ""}
        onUpdate={() => {}}
        onUpdateEndpoint={() => {}}
      />,
    );
  }

  function disabledOptions(label: string): string[] {
    const select = screen.getByLabelText(label) as HTMLSelectElement;
    return Array.from(select.options)
      .filter((option) => option.disabled)
      .map((option) => option.value);
  }

  it("a role or medium one endpoint segment does not carry is disabled", () => {
    renderLinkEditor(true);
    expect(disabledOptions("Endpoint A terminal role")).toEqual([
      "isl",
      "crosslink",
      "backbone",
    ]);
    expect(disabledOptions("Endpoint A medium")).toEqual(["optical"]);
    expect(disabledOptions("Endpoint B terminal role")).toEqual([
      "isl",
      "crosslink",
      "backbone",
    ]);
  });

  it("an unresolved world disables nothing", () => {
    renderLinkEditor(false);
    expect(disabledOptions("Endpoint A terminal role")).toEqual([]);
    expect(disabledOptions("Endpoint A medium")).toEqual([]);
  });
});

describe("link and domain cards show the grammar id and offer no rename", () => {
  afterEach(cleanup);

  it("the link card shows its rule id outside any input", () => {
    const placed = sessionSegments({
      segments: [{ id: "leo", source: "nodalarc:constellations/leo.yaml" }],
    });
    const endpoint = {
      segment_id: "leo",
      tag: null,
      role: "isl" as const,
      medium: "optical" as const,
      min_elevation_deg: null,
    };
    const rule: BuilderVisualLinkRule = {
      rule_id: "fabric-1",
      enabled: true,
      a: endpoint,
      b: { ...endpoint },
      topology_mode: "nearest_n",
      topology_n: 2,
      max_range_km: null,
    };
    render(
      <LinkRuleWindow
        authoring={AUTHORING_FACTS}
        placed={placed}
        listed={{
          id: "fabric-1",
          accepted: { rule_id: "fabric-1", segment_ids: ["leo"], widget: rule },
          pending: null,
          card: rule,
          segmentIds: ["leo"],
        }}
        appliedRevision={1}
        onEdit={async () => undefined}
        onDescribe={() => Promise.reject(new Error("a card rule is not described"))}
        onHold={() => () => undefined}
        checking={false}
        segments={null}
        allocation={null}
        onRepoint={async () => ""}
        onRemove={null}
      />,
    );
    expect(screen.getByText("fabric-1")).toBeTruthy();
    expect(screen.queryByDisplayValue("fabric-1")).toBeNull();
  });

  it("the domain card shows its domain id outside any input", () => {
    const placed = sessionSegments({
      segments: [{ id: "leo", source: "nodalarc:constellations/leo.yaml" }],
    });
    const domain: BuilderVisualRoutingDomain = {
      domain_id: "core-1",
      protocol: "isis",
      member_segment_ids: ["leo"],
      hello_interval_s: null,
      hold_interval_s: null,
    };
    render(
      <RoutingDomainWindow
        authoring={AUTHORING_FACTS}
        placed={placed}
        listed={{
          id: "core-1",
          accepted: {
            domain_id: "core-1",
            protocol: "isis",
            segment_ids: ["leo"],
            widget: domain,
          },
          pending: null,
          card: domain,
          protocol: "isis",
        }}
        appliedRevision={1}
        onEdit={async () => undefined}
        onDescribe={() => Promise.reject(new Error("a card domain is not described"))}
        onHold={() => () => undefined}
        checking={false}
        onRemove={null}
      />,
    );
    expect(screen.getByText("core-1")).toBeTruthy();
    expect(screen.queryByDisplayValue("core-1")).toBeNull();
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

  it("NullableNumberField: a whole-number field commits no fraction", () => {
    let value: number | null = 2;
    render(
      <NullableNumberField
        label="hello"
        placeholder="engine default"
        value={value}
        min={1}
        integer
        onChange={(v) => {
          value = v;
        }}
      />,
    );
    const box = screen.getByPlaceholderText("engine default");
    fireEvent.change(box, { target: { value: "2.5" } });
    expect(value).toBe(2); // a fraction is not committed, and never rounded
    fireEvent.change(box, { target: { value: "3" } });
    expect(value).toBe(3);
  });
});

describe("the YAML buffer locks graphical editing only around an open draft's text", () => {
  const buffer = {
    text: "",
    appliedText: "",
    generation: 0,
    sentGeneration: 0,
    applyFailure: null,
    dirty: false,
    applied: false,
    canonicalizationRequired: false,
    canonicalizationAccepted: false,
    issues: [],
  };

  it("leaves the Builder usable when no draft is open", () => {
    // A Builder with nothing open must still let the user open or create a session.
    expect(yamlBlocksGraphicalEditing(false, buffer)).toBe(false);
  });

  it("locks while the open draft's YAML is unapplied, dirty or uncanonicalized", () => {
    expect(yamlBlocksGraphicalEditing(true, buffer)).toBe(true);
    expect(yamlBlocksGraphicalEditing(true, { ...buffer, applied: true, dirty: true })).toBe(true);
    expect(
      yamlBlocksGraphicalEditing(true, {
        ...buffer,
        applied: true,
        canonicalizationRequired: true,
      }),
    ).toBe(true);
    expect(yamlBlocksGraphicalEditing(true, { ...buffer, applied: true })).toBe(false);
  });
});

describe("a gesture that replaces the draft asks first only when work would be lost", () => {
  const draft = { target_ref: "user:sessions/s.yaml", draft_revision: 3 };
  const clean = { targetRef: "user:sessions/s.yaml", revision: 3 };
  const quiet = {
    pendingEditCount: 0,
    yamlDirty: false,
  };

  it("a draft as it was opened, created or saved holds no unsaved work", () => {
    expect(draftHoldsUnsavedWork({ draft, clean, ...quiet })).toBe(false);
    expect(draftHoldsUnsavedWork({ draft: null, clean: null, ...quiet })).toBe(false);
  });

  it("an accepted edit, a pending edit or unapplied YAML is unsaved work", () => {
    expect(draftHoldsUnsavedWork({ draft: { ...draft, draft_revision: 4 }, clean, ...quiet })).toBe(
      true,
    );
    expect(draftHoldsUnsavedWork({ draft, clean, ...quiet, pendingEditCount: 1 })).toBe(true);
    expect(draftHoldsUnsavedWork({ draft, clean, ...quiet, yamlDirty: true })).toBe(true);
    expect(draftHoldsUnsavedWork({ draft, clean: null, ...quiet })).toBe(true);
  });
});

describe("the anatomy guide answers what-next in any order", () => {
  afterEach(cleanup);
  const space = (id: string): JsonValue => ({ id, source: "nodalarc:constellations/x.yaml" });
  const document = (name: string, segments: JsonValue[]) => ({ session: { name }, segments });
  const station = (namespace: string) => ({
    kind: "ground_station" as const,
    namespace,
    routing_instances: [],
  });
  const guideProps = (overrides: Partial<ComponentProps<typeof BuildGuide>> = {}) => ({
    compiled: null,
    refusal: null,
    hasAcceptedSession: true,
    placeholderName: null,
    saved: null,
    onAddConstellation: () => {},
    onAddGround: () => {},
    onAddDomain: () => {},
    onOpenSession: () => {},
    onOpenSegment: () => {},
    ...overrides,
  });

  it("uses the backend placeholder fact instead of guessing from the generated name", () => {
    render(
      <BuildGuide
        {...guideProps({
          compiled: { canonical_session_json: document("backend-generated-name", [space("leo")]) },
          placeholderName: "backend-generated-name",
        })}
      />,
    );

    expect(screen.getByText("name it — real time unless you say otherwise")).toBeTruthy();
    expect(screen.queryByText("backend-generated-name")).toBeNull();
  });

  it("done rows show counts, not health claims", () => {
    render(
      <BuildGuide
        {...guideProps({ compiled: { canonical_session_json: document("named-session", [space("leo")]) } })}
      />,
    );
    expect(screen.getByText("1 segment · add more")).toBeTruthy();
    expect(screen.getByText("named-session")).toBeTruthy();
  });

  it("the resolved distinct-namespace site count is shown as-is", () => {
    // A single two-node site resolves to ONE namespace → count 1, not 2.
    render(
      <BuildGuide
        {...guideProps({
          compiled: {
            canonical_session_json: document("named", [space("leo")]),
            resolved_preview: { nodes: [station("denver"), station("denver")] },
          },
        })}
      />,
    );
    expect(screen.getByText("1 site · add more")).toBeTruthy();
  });

  it("before the world resolves, the rows that need it show the refusal and no count", () => {
    render(
      <BuildGuide
        {...guideProps({
          compiled: { canonical_session_json: document("named", [space("leo")]), resolved_preview: null },
          refusal: "link rule leo_isl selects no node",
        })}
      />,
    );
    expect(screen.getByText("not resolved — link rule leo_isl selects no node")).toBeTruthy();
    expect(
      screen.getByText("not resolved — link rule leo_isl selects no node · routing omitted"),
    ).toBeTruthy();
    expect(screen.getByText("1 segment · add more")).toBeTruthy();
  });

  it("an accepted session the backend has not answered for yet shows no count", () => {
    render(<BuildGuide {...guideProps()} />);
    expect(screen.getAllByText("not checked yet")).toHaveLength(5);
  });

  it("a new draft the backend refused shows the refusal, not what to add first", () => {
    // A new draft can hold segments before it holds an accepted session; once
    // the backend has answered, its refusal is shown.
    render(
      <BuildGuide
        {...guideProps({
          compiled: { canonical_session_json: null, resolved_preview: null },
          refusal: "Ground segments require at least one site",
          hasAcceptedSession: false,
        })}
      />,
    );
    expect(
      screen.getAllByText("not resolved — Ground segments require at least one site"),
    ).toHaveLength(5);
    expect(screen.queryByText("none yet — satellites carry the traffic")).toBeNull();
  });

  it("an unresolved session still says routing was omitted", () => {
    render(
      <BuildGuide
        {...guideProps({
          compiled: { canonical_session_json: document("named", [space("leo")]), resolved_preview: null },
          refusal: "link rule leo_isl selects no node",
        })}
      />,
    );
    expect(
      screen.getByText("not resolved — link rule leo_isl selects no node · routing omitted"),
    ).toBeTruthy();
  });

  it("a session with routing omitted and no router says it runs no routing", () => {
    render(
      <BuildGuide
        {...guideProps({
          compiled: {
            canonical_session_json: document("named", [space("leo")]),
            resolved_preview: { nodes: [station("denver")] },
          },
        })}
      />,
    );
    expect(
      screen.getByText("none · routing omitted, and no node runs a routing workload"),
    ).toBeTruthy();
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
      resolved_semantic: null,
    },
    blockers: [],
  };

  it("deploys only when the saved revision matches the settled compile", () => {
    expect(
      canDeploy({
        savedVerdict,
        settledDocumentDigest: "doc-x",
        settledDependencyDigest: "dep-x",
        pendingEditCount: 0,
      }),
    ).toEqual({ ok: true, reason: null });
  });

  it("refuses without a save", () => {
    const gate = canDeploy({
      savedVerdict: null,
      settledDocumentDigest: "doc-x",
      settledDependencyDigest: "dep-x",
      pendingEditCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/save the session first/);
  });

  it("fails closed when no compile has settled (cleared or refused)", () => {
    const gate = canDeploy({
      savedVerdict,
      settledDocumentDigest: null,
      settledDependencyDigest: null,
      pendingEditCount: 0,
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
            source_ref: null,
            json_pointer: null,
            draft_path: null,
            source_line: null,
            source_column: null,
            subject_kind: null,
            related_refs: [],
          },
        ],
      },
      settledDocumentDigest: "doc-x",
      settledDependencyDigest: "dep-x",
      pendingEditCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/no satellites/);
  });

  it("refuses while link and routing edits are pending", () => {
    const one = canDeploy({
      savedVerdict,
      settledDocumentDigest: "doc-x",
      settledDependencyDigest: "dep-x",
      pendingEditCount: 1,
    });
    expect(one).toEqual({
      ok: false,
      reason: "apply or discard the pending edit first",
    });
    const three = canDeploy({
      savedVerdict,
      settledDocumentDigest: "doc-x",
      settledDependencyDigest: "dep-x",
      pendingEditCount: 3,
    });
    expect(three.reason).toBe("apply or discard the 3 pending edits first");
  });

  it("names the staleness when the saved copy is behind the edits", () => {
    const gate = canDeploy({
      savedVerdict,
      settledDocumentDigest: "doc-y",
      settledDependencyDigest: "dep-x",
      pendingEditCount: 0,
    });
    expect(gate.ok).toBe(false);
    expect(gate.reason).toMatch(/behind your edits/);
  });
});

