import { describe, expect, it, vi } from "vitest";

vi.mock("../positions", () => ({
  getNodeWorldPosition: (id: string, out: { x: number; y: number; z: number }) => {
    // Two known nodes resolve; anything else is unresolved.
    const table: Record<string, [number, number, number]> = {
      a: [1, 2, 3],
      b: [4, 5, 6],
    };
    const p = table[id];
    if (!p) return false;
    [out.x, out.y, out.z] = p;
    return true;
  },
}));

const { collectHopPositions } = await import("../FlowPaths");

describe("collectHopPositions", () => {
  it("hides a single-hop path instead of drawing a zero-length line", () => {
    // The Earth-to-Luna QUIC trace resolves to one hop (endpoints on host
    // LANs). A one-hop path must not render — a zero-segment fat line
    // throws in three's computeLineDistances.
    const buffer = new Float32Array(3);
    expect(collectHopPositions(["a"], buffer)).toBe(false);
  });

  it("hides a path with any unresolved hop", () => {
    const buffer = new Float32Array(6);
    expect(collectHopPositions(["a", "missing"], buffer)).toBe(false);
  });

  it("fills the buffer and renders a two-hop path", () => {
    const buffer = new Float32Array(6);
    expect(collectHopPositions(["a", "b"], buffer)).toBe(true);
    expect(Array.from(buffer)).toEqual([1, 2, 3, 4, 5, 6]);
  });
});
