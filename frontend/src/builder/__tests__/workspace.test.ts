// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Browser-only workspace behavior.
 *
 * Persisted session assembly and import round trips are deliberately absent:
 * those contracts belong to VS-API's visual draft service.
 */

import { describe, expect, it } from "vitest";
import {
  groundWarnings,
  isCurrentWorkspace,
  linkWarnings,
  newRefSegment,
  parseSiteLines,
  reseedCounters,
  routingWarnings,
} from "../workspace";
import {
  EARTH_BODY_REF,
  defaultRoutingDomain,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
  testGroundMember,
} from "./fixtures/workspaceFixtures";

const SPACE_NODE = "nodalarc:nodes/space/test.yaml";
const GROUND_NODE = "nodalarc:nodes/ground/test.yaml";

describe("workspace interaction state", () => {
  it("represents an empty visual workspace", () => {
    const workspace = newWorkspace("draft");
    expect(workspace.space).toEqual([]);
    expect(workspace.ground).toEqual([]);
  });

  it("reseeds browser-owned reference counters after restore", () => {
    const restored = newWorkspace("restored");
    restored.space_refs.push({ ...newRefSegment("nodalarc:constellations/x.yaml", "X"), segment_id: "lib-900" });
    reseedCounters(restored);
    expect(Number(newRefSegment("nodalarc:constellations/y.yaml", "Y").segment_id.split("-")[1])).toBeGreaterThan(900);
  });

  it("authors walker phasing explicitly for a new generated constellation", () => {
    expect(newDraftConstellation(SPACE_NODE)).toMatchObject({
      planes: 3,
      phasing_mode: "walker_delta",
      phase_offset_deg: 0,
    });
  });

  it("rejects recovered application state missing explicit phasing", () => {
    const workspace = newWorkspace("old-draft");
    const constellation = newDraftConstellation(SPACE_NODE);
    workspace.space.push(constellation);
    expect(isCurrentWorkspace(workspace)).toBe(true);
    const stale = structuredClone(workspace) as unknown as {
      space: Array<Record<string, unknown>>;
    };
    delete stale.space[0]?.phasing_mode;
    expect(isCurrentWorkspace(stale)).toBe(false);
  });
});

describe("ground interaction helpers", () => {
  it("parses typed site-location intent without allocating configuration", () => {
    const parsed = parseSiteLines("Denver, 39.7, -104.9\nPerth, -31.9, 115.8");
    expect(parsed.errors).toEqual([]);
    expect(parsed.rows).toEqual([
      { name: "Denver", lat_deg: 39.7, lon_deg: -104.9, alt_m: 0 },
      { name: "Perth", lat_deg: -31.9, lon_deg: 115.8, alt_m: 0 },
    ]);
  });

  it("keeps local guidance advisory while backend compile owns save refusal", () => {
    const ground = newDraftGroundSet(GROUND_NODE, {});
    ground.members = [testGroundMember(ground, "Bad", 95, 181)];
    expect(groundWarnings(ground).join(" ")).toMatch(/latitude|longitude/);
  });

  it("rejects recovered ground state missing explicit boresight maps", () => {
    const workspace = newWorkspace("old-ground-draft");
    workspace.ground.push(newDraftGroundSet(GROUND_NODE, {}));
    expect(isCurrentWorkspace(workspace)).toBe(true);
    const stale = structuredClone(workspace) as unknown as {
      ground: Array<{ stamp: Record<string, unknown> }>;
    };
    delete stale.ground[0]?.stamp.boresights;
    expect(isCurrentWorkspace(stale)).toBe(false);
  });

});

describe("topology guidance", () => {
  it("keeps body and orbit geometry as authored visual state", () => {
    const constellation = newDraftConstellation(SPACE_NODE);
    expect(constellation.orbit.central_body).toBe(EARTH_BODY_REF);
    constellation.orbit.altitude_km = -1;
    expect(constellation.orbit.altitude_km).toBe(-1);
  });

  it("preserves segment identities through link and routing interactions", () => {
    const workspace = newWorkspace("topology");
    const space = newDraftConstellation(SPACE_NODE);
    const ground = newDraftGroundSet(GROUND_NODE, {});
    workspace.space.push(space);
    workspace.ground.push(ground);
    workspace.links.push({
      rule_id: "backend-rule-1",
      label: "Ground access",
      enabled: true,
      a: {
        segment_id: ground.segment_id,
        tag: null,
        role: "access",
        medium: "rf",
        min_elevation_deg: 25,
      },
      b: {
        segment_id: space.segment_id,
        tag: null,
        role: "access",
        medium: "rf",
        min_elevation_deg: null,
      },
      topology_mode: "visible_candidates",
      topology_n: 1,
      max_range_km: null,
    });
    workspace.routing_domains.push(defaultRoutingDomain(workspace));
    expect(workspace.links[0]?.a.segment_id).toBe(ground.segment_id);
    expect(workspace.routing_domains[0]?.member_segment_ids).toContain(space.segment_id);
    expect(linkWarnings(workspace).join(" ")).not.toContain("held out");
    expect(routingWarnings(workspace).join(" ")).not.toContain("held out");
  });
});
