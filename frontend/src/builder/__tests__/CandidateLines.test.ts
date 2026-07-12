// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** the candidate-line geometry buffers must rebuild whenever the pairs
 *  array is a fresh identity OR the mounted geometry lost its position
 *  attribute — not only when the pair COUNT changed. Keying on length alone left
 *  stale kind colors after a same-length swap and an empty buffer after a
 *  remount (the N->0->N path).
 */

import { describe, expect, it } from "vitest";
import { candidateBufferStale, candidateColors } from "../CandidateLines";
import type { CandidatePair } from "../candidates";

const pair = (kind: string, a: string, b: string): CandidatePair => ({ rule_id: "r", kind, a, b });

describe("buffers rebuild on identity change or remount", () => {
  it("a new pairs identity is stale even at equal length", () => {
    const built = [pair("access", "g", "s")];
    const next = [pair("isl", "x", "y")]; // same length, different array + kind
    expect(candidateBufferStale(built, next, true)).toBe(true);
  });

  it("a missing position attribute is stale — a remount redraws", () => {
    const list = [pair("access", "g", "s")];
    expect(candidateBufferStale(list, list, false)).toBe(true);
  });

  it("the same list with a live attribute is not stale — no needless rebuild", () => {
    const list = [pair("access", "g", "s")];
    expect(candidateBufferStale(list, list, true)).toBe(false);
  });

  it("N->0->N recomputes to a fresh array, so it redraws", () => {
    const first = [pair("access", "g", "s")];
    const afterZero = [pair("access", "g", "s")]; // a distinct array from the recompute
    expect(candidateBufferStale(first, afterZero, true)).toBe(true);
  });

  it("kind colors follow the pairs — a same-length recompute takes the new kind's color", () => {
    const access = candidateColors([pair("access", "g", "s")]);
    const isl = candidateColors([pair("isl", "a", "b")]);
    expect(Array.from(access.slice(0, 3))).not.toEqual(Array.from(isl.slice(0, 3)));
    // Both vertices of a pair carry the pair's color.
    expect(Array.from(access.slice(0, 3))).toEqual(Array.from(access.slice(3, 6)));
    // Recomputing the same-length list with a different kind yields that kind's color.
    const recomputed = candidateColors([pair("isl", "g", "s")]);
    expect(Array.from(recomputed.slice(0, 3))).toEqual(Array.from(isl.slice(0, 3)));
  });
});
