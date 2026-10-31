// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The bulk site paste a ground segment's editor reads. */

import { describe, expect, it } from "vitest";
import { parseSiteLines } from "../GroundEditor";

describe("ground interaction helpers", () => {
  it("parses typed site-location intent without allocating configuration", () => {
    const parsed = parseSiteLines("Denver, 39.7, -104.9\nPerth, -31.9, 115.8");
    expect(parsed.errors).toEqual([]);
    expect(parsed.rows).toEqual([
      { name: "Denver", lat_deg: 39.7, lon_deg: -104.9 },
      { name: "Perth", lat_deg: -31.9, lon_deg: 115.8 },
    ]);
  });
});
