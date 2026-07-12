import { describe, expect, it } from "vitest";
import { nextSiteMember } from "../GroundStation";
import type { NodeState } from "../../../types";

function gs(id: string, namespace: string): NodeState {
  return {
    node_id: id,
    node_type: "ground_station",
    namespace,
  } as unknown as NodeState;
}

const NODES = [
  gs("denver-gw1", "earth-us-co-denver"),
  gs("denver-gw2", "earth-us-co-denver"),
  gs("madrid-gw1", "earth-es-madrid"),
];

describe("nextSiteMember (stacked-site click cycling)", () => {
  it("selects the first member on a fresh click", () => {
    expect(nextSiteMember(NODES, "denver-gw2", null)).toBe("denver-gw1");
    expect(nextSiteMember(NODES, "denver-gw2", "madrid-gw1")).toBe("denver-gw1");
  });

  it("cycles through site members on repeated clicks", () => {
    expect(nextSiteMember(NODES, "denver-gw1", "denver-gw1")).toBe("denver-gw2");
    expect(nextSiteMember(NODES, "denver-gw1", "denver-gw2")).toBe("denver-gw1");
  });

  it("is a plain selection for single-gateway sites", () => {
    expect(nextSiteMember(NODES, "madrid-gw1", null)).toBe("madrid-gw1");
    expect(nextSiteMember(NODES, "madrid-gw1", "madrid-gw1")).toBe("madrid-gw1");
  });
});

describe("siteLabelRepresentatives", () => {
  it("shows the selected member's label for a stacked site", async () => {
    const { siteLabelRepresentatives } = await import("../Labels");
    const reps = siteLabelRepresentatives(NODES, "denver-gw2");
    expect(reps.has("denver-gw2")).toBe(true);
    expect(reps.has("denver-gw1")).toBe(false);
    expect(reps.has("madrid-gw1")).toBe(true);
  });

  it("falls back to the first member when nothing in the site is selected", async () => {
    const { siteLabelRepresentatives } = await import("../Labels");
    const reps = siteLabelRepresentatives(NODES, null);
    expect(reps.has("denver-gw1")).toBe(true);
    expect(reps.has("denver-gw2")).toBe(false);
  });
});

describe("overlapsPlaced", () => {
  it("detects intersecting label boxes and clears disjoint ones", async () => {
    const { overlapsPlaced } = await import("../Labels");
    const placed = [{ x: 100, y: 100, w: 60, h: 14 }];
    expect(overlapsPlaced(placed, { x: 130, y: 105, w: 60, h: 14 })).toBe(true);
    expect(overlapsPlaced(placed, { x: 161, y: 100, w: 60, h: 14 })).toBe(false);
    expect(overlapsPlaced(placed, { x: 100, y: 115, w: 60, h: 14 })).toBe(false);
    expect(overlapsPlaced([], { x: 0, y: 0, w: 60, h: 14 })).toBe(false);
  });
});

function node(
  id: string,
  node_type: string,
  reference_body: string,
  segment_id: string | null,
): NodeState {
  return { node_id: id, node_type, reference_body, segment_id } as unknown as NodeState;
}

// earth carries one satellite segment AND one ground segment; luna is
// satellite-only; mars is ground-only. That spread exercises every mixed-toggle
// case in one fixture.
const EARTH_SAT = node("earth-sat-1", "satellite", "earth", "earth-leo");
const EARTH_GS = node("earth-gs-1", "ground_station", "earth", "earth-ground");
const LUNA_SAT = node("luna-sat-1", "satellite", "luna", "luna-orbit");
const MARS_GS = node("mars-gs-1", "ground_station", "mars", "mars-ground");
const FOLD_NODES = [EARTH_SAT, EARTH_GS, LUNA_SAT, MARS_GS];

function segs(out: Map<string, Set<string>>, body: string): string[] {
  return [...(out.get(body) ?? [])].sort();
}

describe("collectFoldSegments — body-fold accounting", () => {
  it("with both classes on, counts every sat and GS segment and probes every body", async () => {
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    const probe = new Map<string, string>();
    collectFoldSegments(FOLD_NODES, true, true, out, probe);
    expect(segs(out, "earth")).toEqual(["earth-ground", "earth-leo"]);
    expect(segs(out, "luna")).toEqual(["luna-orbit"]);
    expect(segs(out, "mars")).toEqual(["mars-ground"]);
    // A probe is recorded for every body (the first node seen), class-agnostic.
    expect(probe.get("earth")).toBe("earth-sat-1");
    expect(probe.get("luna")).toBe("luna-sat-1");
    expect(probe.get("mars")).toBe("mars-gs-1");
  });

  it("default-toggle count equals the class-blind count (flag-off inertness)", async () => {
    // The previous fold counted every node's segment regardless of label class.
    // At default toggles (both on) the new class-aware count must reproduce it
    // exactly, so a never-toggling live user sees identical folding.
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    collectFoldSegments(FOLD_NODES, true, true, out, new Map());
    const classBlind = new Map<string, Set<string>>();
    for (const n of FOLD_NODES) {
      if (!n.segment_id) continue;
      let set = classBlind.get(n.reference_body!);
      if (!set) classBlind.set(n.reference_body!, (set = new Set()));
      set.add(n.segment_id);
    }
    expect(out.size).toBe(classBlind.size);
    for (const [body, want] of classBlind) {
      expect(segs(out, body)).toEqual([...want].sort());
    }
  });

  it("satellite labels off drops every satellite segment from the count", async () => {
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    collectFoldSegments(FOLD_NODES, false, true, out, new Map());
    expect(segs(out, "earth")).toEqual(["earth-ground"]); // sat segment gone
    expect(out.get("luna")?.size ?? 0).toBe(0); // luna is sat-only → nothing counts
    expect(segs(out, "mars")).toEqual(["mars-ground"]);
  });

  it("ground labels off drops every ground segment from the count", async () => {
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    collectFoldSegments(FOLD_NODES, true, false, out, new Map());
    expect(segs(out, "earth")).toEqual(["earth-leo"]); // GS segment gone
    expect(segs(out, "luna")).toEqual(["luna-orbit"]);
    expect(out.get("mars")?.size ?? 0).toBe(0); // mars is GS-only → nothing counts
  });

  it("with both classes off nothing counts, yet every body is still probed", async () => {
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    const probe = new Map<string, string>();
    collectFoldSegments(FOLD_NODES, false, false, out, probe);
    for (const body of ["earth", "luna", "mars"]) {
      // counted.size === 0 is the caller's skip — the body does NOT fold.
      expect(out.get(body)?.size ?? 0).toBe(0);
      expect(probe.has(body)).toBe(true);
    }
  });

  it("a body whose members all lack a segment_id never folds", async () => {
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    const probe = new Map<string, string>();
    const noSeg = [
      node("pluto-sat", "satellite", "pluto", null),
      node("pluto-gs", "ground_station", "pluto", null),
    ];
    collectFoldSegments(noSeg, true, true, out, probe);
    expect(out.get("pluto")?.size ?? 0).toBe(0);
    expect(probe.get("pluto")).toBe("pluto-sat"); // still projectable, just unfolded
  });

  it("counts distinct segments — the chip dedups members sharing a segment", async () => {
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    const dup = [
      node("a", "satellite", "earth", "seg-1"),
      node("b", "satellite", "earth", "seg-1"),
      node("c", "satellite", "earth", "seg-2"),
    ];
    collectFoldSegments(dup, true, true, out, new Map());
    expect(out.get("earth")?.size).toBe(2);
  });

  it("reuses the out/probe maps across frames — inner Sets cleared, not reallocated", async () => {
    const { collectFoldSegments } = await import("../Labels");
    const out = new Map<string, Set<string>>();
    const probe = new Map<string, string>();
    collectFoldSegments(FOLD_NODES, true, true, out, probe);
    const earthSet = out.get("earth");
    // Next frame: earth keeps only its satellite; the GS and mars/luna depart.
    collectFoldSegments([EARTH_SAT], true, true, out, probe);
    expect(out.get("earth")).toBe(earthSet); // same Set instance — no realloc
    expect([...earthSet!]).toEqual(["earth-leo"]); // stale GS segment cleared
    expect(probe.has("mars")).toBe(false); // departed body's probe cleared
    expect(probe.has("luna")).toBe(false);
  });
});

describe("isFolded — fold visibility predicate (selection survives the fold)", () => {
  const bodyOf = new Map([
    ["luna-sat-1", "luna"],
    ["earth-sat-1", "earth"],
  ]);
  const lunaFolded = new Map<string, unknown>([["luna", {}]]);

  it("hides a non-selected node whose body is folded", async () => {
    const { isFolded } = await import("../Labels");
    expect(isFolded(null, bodyOf, lunaFolded, "luna-sat-1")).toBe(true);
  });

  it("keeps the selected node's label even when its body is folded", async () => {
    const { isFolded } = await import("../Labels");
    expect(isFolded("luna-sat-1", bodyOf, lunaFolded, "luna-sat-1")).toBe(false);
  });

  it("keeps a node whose body is not folded", async () => {
    const { isFolded } = await import("../Labels");
    expect(isFolded(null, bodyOf, lunaFolded, "earth-sat-1")).toBe(false);
  });

  it("keeps a node with no known body", async () => {
    const { isFolded } = await import("../Labels");
    expect(isFolded(null, new Map(), lunaFolded, "orphan")).toBe(false);
  });
});
