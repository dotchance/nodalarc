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
