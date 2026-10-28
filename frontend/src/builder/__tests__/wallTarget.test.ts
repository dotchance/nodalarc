// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** wallTarget: map a resolve refusal's subject back to the editor window
 *  that owns it. A rule or domain is matched among the objects the backend
 *  lists, a segment against the PREVIEW workspace. A subject with no window to
 *  show it returns null — the caller then shows the session-level wall, never
 *  dropping the refusal.
 */
import { describe, expect, it } from "vitest";
import { wallTarget } from "../wallTarget";
import { listSessionObjects } from "../sessionListing";
import { targetKey } from "../useEditorWindows";
import type { Workspace } from "../workspace";
import {
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
  testGroundMember,
} from "./fixtures/workspaceFixtures";
import {
  acceptedDomain,
  acceptedRule,
  domainCard,
  domainAddress,
  pendingAnswer,
  ruleAddress,
  ruleCard,
  sessionListing,
} from "./fixtures/listingFixtures";
import type { BuilderResolveError } from "../builderTypes";

/** A workspace with one space constellation and one populated ground segment. */
function placedWorkspace(): Workspace {
  const ws = newWorkspace("wall-test");
  ws.space.push(newDraftConstellation("nodalarc:nodes/space/x.yaml"));
  const ground = newDraftGroundSet("nodalarc:nodes/ground/gw.yaml", {});
  ground.members = [testGroundMember(ground, "Denver", 39.7, -104.9)];
  ws.ground.push(ground);
  return ws;
}

/** The backend's listing of an access rule a card writes back, one rule no
 *  card writes back, and a routing domain a card writes back. */
const LISTING = sessionListing({
  link_rules: [
    acceptedRule("gw-access", ["gw", "leo"], ruleCard("gw-access", "gw", "leo", "access")),
    acceptedRule("leo-fabric", ["leo"], null),
  ],
  routing_domains: [
    acceptedDomain("core", "isis", ["leo", "gw"], domainCard("core", "isis", ["leo", "gw"])),
  ],
});

describe("wallTarget", () => {
  it("routes a link_rule subject to the window of the listed rule with that id", () => {
    const err: BuilderResolveError = {
      error: "rule refused",
      subject: { kind: "link_rule", id: "gw-access" },
    };
    const target = { kind: "link" as const, id: "gw-access" };
    expect(wallTarget(placedWorkspace(), listSessionObjects(LISTING, []), err)).toEqual({
      target,
      key: targetKey(target),
    });
  });

  it("routes a routing_domain subject to its domain window", () => {
    const err: BuilderResolveError = {
      error: "domain refused",
      subject: { kind: "routing_domain", id: "core" },
    };
    const target = { kind: "domain" as const, id: "core" };
    expect(wallTarget(placedWorkspace(), listSessionObjects(LISTING, []), err)).toEqual({
      target,
      key: targetKey(target),
    });
  });

  it("routes a rule no card writes back, or a pending rule, to its window", () => {
    const listed = listSessionObjects(LISTING, [pendingAnswer(ruleAddress("gw-access"), null)]);
    for (const id of ["leo-fabric", "gw-access"]) {
      const target = { kind: "link" as const, id };
      expect(
        wallTarget(placedWorkspace(), listed, {
          error: "refused",
          subject: { kind: "link_rule", id },
        }),
      ).toEqual({ target, key: targetKey(target) });
    }
  });

  it("leaves to the session-level wall a subject no window shows", () => {
    // core holds a pending edit, so its window cannot show it.
    const listed = listSessionObjects(LISTING, [pendingAnswer(domainAddress("core"), null)]);
    expect(
      wallTarget(placedWorkspace(), listed, {
        error: "refused",
        subject: { kind: "routing_domain", id: "core" },
      }),
    ).toBeNull();
  });

  it("a segment subject falls through space→ground", () => {
    const ws = placedWorkspace();
    const groundId = ws.ground[0]!.segment_id;
    const err: BuilderResolveError = { error: "segment refused", segment_id: groundId };
    expect(wallTarget(ws, listSessionObjects(LISTING, []), err)?.target).toEqual({
      kind: "ground",
      id: groundId,
    });
  });

  it("an unknown subject kind and a no-match subject both return null (session-level wall)", () => {
    const ws = placedWorkspace();
    const listed = listSessionObjects(LISTING, []);
    expect(
      wallTarget(ws, listed, { error: "x", subject: { kind: "session", id: "s" } }),
    ).toBeNull();
    expect(
      wallTarget(ws, listed, { error: "x", subject: { kind: "link_rule", id: "no-such-rule" } }),
    ).toBeNull();
    expect(wallTarget(ws, listed, { error: "x", segment_id: "does-not-exist" })).toBeNull();
    expect(wallTarget(ws, listed, { error: "x" })).toBeNull(); // no subject, no segment
    expect(wallTarget(null, listed, { error: "x" })).toBeNull(); // no preview
    expect(wallTarget(ws, listed, null)).toBeNull(); // no error
  });
});
