/** Typed visual DTO mapping only; persisted grammar is backend-owned. */

import { describe, expect, it } from "vitest";
import { refGroundMember } from "../workspace";
import {
  defaultBoundary,
  defaultRoutingDomain,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
} from "./fixtures/workspaceFixtures";
import {
  structuredDraftFromWorkspace,
  visualWorkspaceFromWorkspace,
  workspaceFromVisualDraft,
} from "../visualWorkspace";

function authoredWorkspace() {
  const workspace = newWorkspace("identity-test");
  const space = newDraftConstellation("nodalarc:nodes/space/test.yaml");
  space.segment_id = "space-stable";
  const ground = newDraftGroundSet("nodalarc:nodes/ground/test.yaml", {});
  ground.stamp.installed = { access: 1 };
  ground.stamp.boresights = { access: { mode: "local_vertical" } };
  ground.segment_id = "ground-stable";
  ground.members = [
    refGroundMember("nodalarc:sites/denver.yaml", "denver", "Denver", null),
  ];
  workspace.space.push(space);
  workspace.ground.push(ground);
  const rule = {
    rule_id: "rule-stable",
    label: "Ground access",
    enabled: true,
    a: {
      segment_id: ground.segment_id,
      tag: null,
      role: "access" as const,
      medium: "rf" as const,
      min_elevation_deg: 25,
    },
    b: {
      segment_id: space.segment_id,
      tag: null,
      role: "access" as const,
      medium: "rf" as const,
      min_elevation_deg: null,
    },
    topology_mode: "visible_candidates" as const,
    topology_n: 1,
    max_range_km: null,
  };
  workspace.links.push(rule);
  const domain = defaultRoutingDomain(workspace);
  domain.domain_id = "domain-stable";
  workspace.routing_domains.push(domain);
  const boundary = defaultBoundary(workspace);
  boundary.boundary_id = "boundary-stable";
  boundary.over_rule_id = rule.rule_id;
  boundary.from_domain_id = domain.domain_id;
  boundary.to_domain_id = domain.domain_id;
  workspace.boundaries.push(boundary);
  return workspace;
}

describe("visual workspace DTO adapter", () => {
  it("round-trips stable segment, rule, domain, and boundary identities", () => {
    const workspace = authoredWorkspace();
    const draft = {
      contract_version: 1 as const,
      draft_revision: 3,
      mode: "structured" as const,
      target_ref: "user:sessions/identity-test.yaml",
      workspace: visualWorkspaceFromWorkspace(workspace),
    };

    const restored = workspaceFromVisualDraft(draft);
    expect(restored.space[0]?.segment_id).toBe("space-stable");
    expect(restored.ground[0]?.segment_id).toBe("ground-stable");
    expect(restored.links[0]?.rule_id).toBe("rule-stable");
    expect(restored.routing_domains[0]?.domain_id).toBe("domain-stable");
    expect(restored.boundaries[0]).toMatchObject({
      boundary_id: "boundary-stable",
      over_rule_id: "rule-stable",
      from_domain_id: "domain-stable",
      to_domain_id: "domain-stable",
    });
    expect(restored.space[0]).toMatchObject({
      phasing_mode: "walker_delta",
      phase_offset_deg: 0,
    });
    expect(restored.ground[0]?.stamp.boresights).toEqual({
      access: { mode: "local_vertical" },
    });
  });

  it("updates only visual application state and retains backend-owned fences", () => {
    const workspace = authoredWorkspace();
    const base = {
      contract_version: 1 as const,
      draft_revision: 4,
      mode: "structured" as const,
      target_ref: "user:sessions/identity-test.yaml",
      expected_session_revision: "session-revision",
      expected_catalog_revisions: [
        {
          ref: "user:nodes/identity-test/custom.yaml",
          expected_revision: "node-revision",
        },
      ],
      catalog_documents: [
        {
          ref: "user:nodes/identity-test/custom.yaml",
          document: { node: { id: "custom" } },
        },
      ],
      workspace: visualWorkspaceFromWorkspace(workspace),
    };

    const updated = structuredDraftFromWorkspace(base, workspace, 5);
    expect(updated.draft_revision).toBe(5);
    expect(updated.expected_session_revision).toBe("session-revision");
    expect(updated.expected_catalog_revisions).toEqual(base.expected_catalog_revisions);
    expect(updated.catalog_documents).toEqual(base.catalog_documents);
    expect(updated.session_yaml).toBeNull();
  });

  it("refuses missing backend semantics instead of selecting browser defaults", () => {
    const workspace = authoredWorkspace();
    const visual = visualWorkspaceFromWorkspace(workspace);
    const link = visual.links![0]!;
    const draft = {
      contract_version: 1 as const,
      draft_revision: 3,
      mode: "structured" as const,
      target_ref: "user:sessions/identity-test.yaml",
      workspace: {
        ...visual,
        links: [{ ...link, a: { ...link.a, role: null } }],
      },
    };

    expect(() => workspaceFromVisualDraft(draft)).toThrow(
      "backend visual draft has no workspace.links.0.a.role",
    );
  });
});
