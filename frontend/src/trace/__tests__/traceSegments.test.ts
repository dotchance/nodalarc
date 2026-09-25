// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, expect, it } from "vitest";
import { traceSegments } from "../traceSegments";

const placedNodes = new Set(["gs-a", "sat-1", "sat-2", "gs-b"]);
const placeable = (hop: string) => placedNodes.has(hop);

describe("traceSegments", () => {
  it("measures every segment between consecutive placed hops", () => {
    expect(traceSegments(["gs-a", "sat-1", "gs-b"], placeable)).toEqual([
      { from: "gs-a", to: "sat-1", measured: true },
      { from: "sat-1", to: "gs-b", measured: true },
    ]);
  });

  it("bridges silent hops and unowned addresses between two placed hops", () => {
    expect(traceSegments(["gs-a", "*", "10.9.9.9", "sat-2", "gs-b"], placeable)).toEqual([
      { from: "gs-a", to: "sat-2", measured: false },
      { from: "sat-2", to: "gs-b", measured: true },
    ]);
  });

  it("bridges a node the view does not show", () => {
    expect(traceSegments(["gs-a", "sat-hidden", "gs-b"], placeable)).toEqual([
      { from: "gs-a", to: "gs-b", measured: false },
    ]);
  });

  it("draws nothing after the last placed hop of a trace that did not arrive", () => {
    expect(traceSegments(["gs-a", "sat-1", "*", "*"], placeable)).toEqual([
      { from: "gs-a", to: "sat-1", measured: true },
    ]);
  });

  it("draws no segment for a trace with fewer than two placed hops", () => {
    // A zero-segment fat line throws in three's computeLineDistances.
    expect(traceSegments(["gs-a"], placeable)).toEqual([]);
    expect(traceSegments(["gs-a", "*", "*"], placeable)).toEqual([]);
  });
});
