/** Direct generated visual DTO state; persisted grammar is backend-owned. */

import { describe, expect, it } from "vitest";
import {
  defaultBoundary,
  defaultRoutingDomain,
  newDraftConstellation,
  newDraftGroundSet,
  newWorkspace,
} from "./fixtures/workspaceFixtures";
import {
  structuredDraftFromWorkspace,
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
    {
      member_id: "member-stable",
      kind: "ref",
      ref: "nodalarc:sites/denver.yaml",
      site_id: "denver",
      label: "Denver",
      summary: null,
      site: null,
      scheduling_override: null,
    },
  ];
  workspace.space.push(space);
  workspace.space_refs.push({
    segment_id: "space-reference-stable",
    source_ref: "nodalarc:constellations/reference.yaml",
    label: "Reference space",
  });
  workspace.ground.push(ground);
  workspace.ground_refs.push({
    segment_id: "ground-reference-stable",
    site_set_ref: "nodalarc:site-sets/reference.yaml",
    label: "Reference ground",
    scheduling: {},
  });
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

describe("visual workspace DTO identity", () => {
  it("round-trips stable segment, rule, domain, and boundary identities", () => {
    const workspace = authoredWorkspace();
    const draft = {
      contract_version: 1 as const,
      draft_revision: 3,
      mode: "structured" as const,
      target_ref: "user:sessions/identity-test.yaml",
      session_name_is_placeholder: false,
      reserved_authoring_ids: [
        "space-stable",
        "ground-stable",
        "space-ref-stable",
        "ground-ref-stable",
        "rule-stable",
        "domain-stable",
        "boundary-stable",
      ],
      workspace,
    };

    const restored = workspaceFromVisualDraft(draft);
    expect(restored.space[0]?.segment_id).toBe("space-stable");
    expect(restored.ground[0]?.segment_id).toBe("ground-stable");
    expect(restored.space_refs[0]?.source_ref).toBe(
      "nodalarc:constellations/reference.yaml",
    );
    expect(restored.ground_refs[0]?.site_set_ref).toBe(
      "nodalarc:site-sets/reference.yaml",
    );
    expect("ref" in restored.space_refs[0]!).toBe(false);
    expect("ref" in restored.ground_refs[0]!).toBe(false);
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
    expect(restored.session_name).toBe("identity-test");
    expect("name" in restored).toBe(false);
  });

  it("updates only visual application state and retains backend-owned fences", () => {
    const workspace = authoredWorkspace();
    const base = {
      contract_version: 1 as const,
      draft_revision: 4,
      mode: "structured" as const,
      target_ref: "user:sessions/identity-test.yaml",
      session_name_is_placeholder: false,
      reserved_authoring_ids: [
        "space-stable",
        "ground-stable",
        "space-ref-stable",
        "ground-ref-stable",
        "rule-stable",
        "domain-stable",
        "boundary-stable",
      ],
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
      workspace,
    };

    const updated = structuredDraftFromWorkspace(base, workspace, 5);
    expect(updated.draft_revision).toBe(5);
    expect(updated.expected_session_revision).toBe("session-revision");
    expect(updated.expected_catalog_revisions).toEqual(base.expected_catalog_revisions);
    expect(updated.catalog_documents).toEqual(base.catalog_documents);
    expect(updated.session_yaml).toBeNull();
  });

  it("preserves incomplete backend semantics without selecting browser defaults", () => {
    const workspace = authoredWorkspace();
    const link = workspace.links[0]!;
    const draft = {
      contract_version: 1 as const,
      draft_revision: 3,
      mode: "structured" as const,
      target_ref: "user:sessions/identity-test.yaml",
      session_name_is_placeholder: false,
      reserved_authoring_ids: [
        "space-stable",
        "ground-stable",
        "space-ref-stable",
        "ground-ref-stable",
        "rule-stable",
        "domain-stable",
        "boundary-stable",
      ],
      workspace: {
        ...workspace,
        links: [{ ...link, a: { ...link.a, role: null } }],
      },
    };

    expect(workspaceFromVisualDraft(draft).links[0]?.a.role).toBeNull();
  });
});
