// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** wallTarget: map a resolve refusal's subject back to the editor window
 *  that owns it, matched against the PREVIEW workspace (so a dirty rename routes
 *  by the dirty draft). An unmatched subject returns null — the caller then
 *  shows the session-level wall, never dropping the refusal.
 */
import { describe, expect, it } from "vitest";
import { wallTarget } from "../wallTarget";
import { targetKey } from "../useEditorWindows";
import {
  emittedDomainId,
  emittedRuleId,
  mintSiteMembers,
  parseSiteLines,
  type Workspace,
} from "../workspace";
import {
  defaultRoutingDomain,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
} from "./fixtures/workspaceFixtures";
import type { BuilderResolveError } from "../builderTypes";

/** A workspace with one space constellation, one populated ground segment, and
 *  a link rule between them. */
function linkedWorkspace(): { ws: Workspace } {
  const ws = newWorkspace("wall-test");
  ws.space.push(newDraftConstellation("nodalarc:nodes/space/x.yaml"));
  const ground = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
  ground.members = mintSiteMembers(ground, parseSiteLines("Denver, 39.7, -104.9").rows);
  ws.ground.push(ground);
  ws.links.push({
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
      segment_id: ws.space[0]!.segment_id,
      tag: null,
      role: "access",
      medium: "rf",
      min_elevation_deg: null,
    },
    topology_mode: "visible_candidates",
    topology_n: 1,
    max_range_km: null,
  });
  return { ws };
}

describe("wallTarget", () => {
  it("routes a link_rule subject to its window by emitted id", () => {
    const { ws } = linkedWorkspace();
    const rule = ws.links[0]!;
    rule.label = "Teleport uplink";
    const err: BuilderResolveError = {
      error: "rule refused",
      subject: { kind: "link_rule", id: emittedRuleId(rule) },
    };
    const target = { kind: "link" as const, id: rule.rule_id };
    expect(wallTarget(ws, err)).toEqual({ target, key: targetKey(target) });
  });

  it("routes a DIRTY-renamed rule by the preview's dirty emitted id", () => {
    // The preview carries the dirty label; the refusal names its emitted id.
    // Matching against the preview (not applied state) is what routes it.
    const { ws } = linkedWorkspace();
    const rule = ws.links[0]!;
    rule.label = "renamed while dirty";
    const dirtyId = emittedRuleId(rule);
    const err: BuilderResolveError = {
      error: "rule refused",
      subject: { kind: "link_rule", id: dirtyId },
    };
    expect(wallTarget(ws, err)?.target).toEqual({ kind: "link", id: rule.rule_id });
    // A stale (applied-label) id would NOT match the preview — routes nowhere.
    expect(
      wallTarget(ws, { error: "x", subject: { kind: "link_rule", id: "some-old-applied-id" } }),
    ).toBeNull();
  });

  it("routes a routing_domain subject to its domain window", () => {
    const { ws } = linkedWorkspace();
    const domain = defaultRoutingDomain(ws);
    domain.label = "Backbone";
    ws.routing_domains.push(domain);
    const err: BuilderResolveError = {
      error: "domain refused",
      subject: { kind: "routing_domain", id: emittedDomainId(domain) },
    };
    const target = { kind: "domain" as const, id: domain.domain_id };
    expect(wallTarget(ws, err)).toEqual({ target, key: targetKey(target) });
  });

  it("a segment subject falls through space→ground", () => {
    const { ws } = linkedWorkspace();
    const groundId = ws.ground[0]!.segment_id;
    const err: BuilderResolveError = { error: "segment refused", segment_id: groundId };
    expect(wallTarget(ws, err)?.target).toEqual({ kind: "ground", id: groundId });
  });

  it("an unknown subject kind and a no-match subject both return null (session-level wall)", () => {
    const { ws } = linkedWorkspace();
    expect(wallTarget(ws, { error: "x", subject: { kind: "session", id: "s" } })).toBeNull();
    expect(wallTarget(ws, { error: "x", segment_id: "does-not-exist" })).toBeNull();
    expect(wallTarget(ws, { error: "x" })).toBeNull(); // no subject, no segment
    expect(wallTarget(null, { error: "x" })).toBeNull(); // no preview
    expect(wallTarget(ws, null)).toBeNull(); // no error
  });
});
