// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** Backend listings of an accepted session and object-edit answers, in the
 *  shapes the backend returns them. */
import type {
  BuilderIssue,
  BuilderMemberAddress,
  BuilderVisualLinkRule,
  BuilderVisualLinkRuleListing,
  BuilderVisualObjectEditResult,
  BuilderVisualRoutingBoundary,
  BuilderVisualRoutingBoundaryListing,
  BuilderVisualRoutingDomain,
  BuilderVisualRoutingDomainListing,
  BuilderVisualSessionListing,
  BuilderWholeListAddress,
  MountRole,
  RoutingProtocol,
} from "../../generated/builderApi";

/** The card value of an enabled rule between two segments. */
export function ruleCard(
  ruleId: string,
  a: string,
  b: string,
  role: MountRole = "isl",
  enabled = true,
): BuilderVisualLinkRule {
  const endpoint = (segmentId: string) => ({
    segment_id: segmentId,
    tag: null,
    role,
    medium: "optical" as const,
    min_elevation_deg: null,
  });
  return {
    rule_id: ruleId,
    enabled,
    a: endpoint(a),
    b: endpoint(b),
    topology_mode: "visible_candidates",
    topology_n: null,
    max_range_km: null,
  };
}

export function acceptedRule(
  ruleId: string,
  segmentIds: ReadonlyArray<string>,
  card: BuilderVisualLinkRule | null,
): BuilderVisualLinkRuleListing {
  return { rule_id: ruleId, segment_ids: segmentIds, widget: card };
}

export function domainCard(
  domainId: string,
  protocol: RoutingProtocol,
  members: ReadonlyArray<string>,
): BuilderVisualRoutingDomain {
  return {
    domain_id: domainId,
    protocol,
    member_segment_ids: members,
    hello_interval_s: null,
    hold_interval_s: null,
  };
}

export function acceptedDomain(
  domainId: string,
  protocol: RoutingProtocol,
  segmentIds: ReadonlyArray<string>,
  card: BuilderVisualRoutingDomain | null,
): BuilderVisualRoutingDomainListing {
  return { domain_id: domainId, protocol, segment_ids: segmentIds, widget: card };
}

export function boundaryCard(
  index: number,
  overRuleId: string,
  from: string,
  to: string,
): BuilderVisualRoutingBoundary {
  return {
    boundary_id: `boundary-${index + 1}`,
    over_rule_id: overRuleId,
    adapter: "static_ip",
    from_domain_id: from,
    to_domain_id: to,
    export_node_loopbacks: true,
  };
}

export function acceptedBoundary(
  index: number,
  overRuleId: string,
  domainIds: ReadonlyArray<string>,
  card: BuilderVisualRoutingBoundary | null,
): BuilderVisualRoutingBoundaryListing {
  return { index, over_rule_id: overRuleId, domain_ids: domainIds, widget: card };
}

export function sessionListing(
  listing: Partial<BuilderVisualSessionListing>,
): BuilderVisualSessionListing {
  return { link_rules: [], routing_domains: [], boundaries: [], ...listing };
}

export const ruleAddress = (id: string): BuilderMemberAddress => ({
  kind: "member",
  list_pointer: "/link_rules",
  id,
});

export const domainAddress = (id: string): BuilderMemberAddress => ({
  kind: "member",
  list_pointer: "/routing/domains",
  id,
});

export const BOUNDARIES_ADDRESS: BuilderWholeListAddress = {
  kind: "whole_list",
  pointer: "/routing/boundaries",
};

/** An issue the backend raises on a pending edit's working JSON. */
export function workingIssue(message: string, jsonPointer: string): BuilderIssue {
  return {
    code: "builder.structural.invalid_document",
    stage: "structural",
    severity: "error",
    message,
    blocks: ["save", "deploy"],
    source_ref: null,
    json_pointer: jsonPointer,
    draft_path: null,
    source_line: null,
    source_column: null,
    subject_kind: null,
    related_refs: [],
  };
}

/** The backend's latest answer for a pending edit at `address`. */
export function pendingAnswer(
  address: BuilderVisualObjectEditResult["address"],
  widget: BuilderVisualObjectEditResult["widget"],
): BuilderVisualObjectEditResult {
  return {
    address,
    working: address.kind === "whole_list" ? [] : address.kind === "member" ? { id: address.id } : {},
    widget,
    control_tree: {
      projection_revision: 1,
      root: {
        control_id: "edit-root",
        json_pointer: "",
        label: "Pending edit",
        required: true,
        present: true,
      },
    },
    issues: [],
  };
}
