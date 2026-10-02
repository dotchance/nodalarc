// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Browser-only workspace behavior.
 *
 * Persisted session assembly and import round trips are deliberately absent:
 * those contracts belong to VS-API's visual draft service.
 */

import { describe, expect, it } from "vitest";
import { groundWarnings, parseSiteLines } from "../workspace";
import { newDraftGroundSet, testGroundMember } from "./fixtures/workspaceFixtures";

const GROUND_NODE = "nodalarc:nodes/ground/test.yaml";

describe("ground interaction helpers", () => {
  it("parses typed site-location intent without allocating configuration", () => {
    const parsed = parseSiteLines("Denver, 39.7, -104.9\nPerth, -31.9, 115.8");
    expect(parsed.errors).toEqual([]);
    expect(parsed.rows).toEqual([
      { name: "Denver", lat_deg: 39.7, lon_deg: -104.9 },
      { name: "Perth", lat_deg: -31.9, lon_deg: 115.8 },
    ]);
  });

  it("keeps local guidance advisory while backend compile owns save refusal", () => {
    const ground = newDraftGroundSet(GROUND_NODE, {});
    ground.members = [testGroundMember(ground, "Bad", 95, 181)];
    expect(groundWarnings(ground).join(" ")).toMatch(/latitude|longitude/);
  });

});
