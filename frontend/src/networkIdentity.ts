// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Runtime identity helpers for node/link classification.
 *
 * Segment-namespaced IDs are not semantic. UI code must classify nodes from
 * `NodeState.node_type` and links from `LinkState.link_type`, not from prefixes.
 */

import type { LinkState, NodeState, PublicLinkType } from "./generated/vsApi";

const NON_GROUND_LINK_TYPES: ReadonlySet<string> = new Set<PublicLinkType>([
  "isl",
  "inter_constellation",
  "inter_body_relay",
]);

export function isGroundNode(node: Pick<NodeState, "node_type">): boolean {
  if (node.node_type === "ground_station") return true;
  if (node.node_type === "satellite") return false;
  throw new Error(`Unknown node_type for frontend classification: ${node.node_type}`);
}

export function selectionTypeForNode(node: Pick<NodeState, "node_type">): "ground_station" | "satellite" {
  return isGroundNode(node) ? "ground_station" : "satellite";
}

export function isGroundLinkType(linkType: PublicLinkType): boolean {
  if (linkType === "ground") return true;
  if (NON_GROUND_LINK_TYPES.has(linkType)) return false;
  // A feed from another VS-API release can carry a kind this page does not know.
  throw new Error(`Unknown link_type for frontend link classification: ${String(linkType)}`);
}

export function isGroundLinkState(link: Pick<LinkState, "link_type">): boolean {
  return isGroundLinkType(link.link_type);
}

export function nodeById(nodes: readonly NodeState[]): Map<string, NodeState> {
  return new Map(nodes.map((node) => [node.node_id, node]));
}

export function selectionTypeForNodeId(
  nodeId: string,
  nodes: readonly NodeState[],
): "ground_station" | "satellite" | null {
  const node = nodes.find((candidate) => candidate.node_id === nodeId);
  return node ? selectionTypeForNode(node) : null;
}
