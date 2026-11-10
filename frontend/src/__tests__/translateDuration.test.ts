// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** The shared duration formatter and a link's latency out of and into one end. */

import { describe, expect, it } from "vitest";
import { formatDuration, formatLinkEndLatency, linkLatencies } from "../translate";

describe("formatDuration", () => {
  it("writes ms below a second, seconds below a minute and minutes beyond", () => {
    // A LEO hop, the Moon, and Mars near its farthest (about 22 minutes).
    expect([2.71, 999.94, 1248.504, 59999, 60000, 1_342_000].map(formatDuration)).toEqual([
      "2.7 ms",
      "999.9 ms",
      "1.249 s",
      "59.999 s",
      "1.00 min",
      "22.37 min",
    ]);
  });
});

describe("linkLatencies", () => {
  const link = { node_a: "a", latency_a_to_b_ms: 1.5, latency_b_to_a_ms: 2.5 };

  it("reads outbound as the direction the named end transmits", () => {
    expect(linkLatencies(link, "a")).toEqual({ outbound: 1.5, inbound: 2.5 });
    expect(linkLatencies(link, "b")).toEqual({ outbound: 2.5, inbound: 1.5 });
  });

  it("writes both directions from the named end", () => {
    expect(formatLinkEndLatency(link, "b")).toBe("out 2.5 ms · in 1.5 ms");
  });
});
