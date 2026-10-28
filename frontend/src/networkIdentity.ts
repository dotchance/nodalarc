// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Runtime identity helpers for node/link classification.
 *
 * Segment-namespaced IDs are not semantic. UI code must classify nodes from
 * `NodeState.node_type` and links from `LinkState.link_type`, not from prefixes.
 */

import type { NodeKind } from "./builder/generated/builderApi";
import type { LinkState, NodeState, PublicLinkType } from "./generated/vsApi";

const GROUND_NODE: Record<NodeKind, boolean> = { ground_station: true, satellite: false };

const GROUND_LINK: Record<PublicLinkType, boolean> = {
  ground: true,
  isl: false,
  inter_constellation: false,
  inter_body_relay: false,
};

export function isGroundNode(node: Pick<NodeState, "node_type">): boolean {
  if (!Object.prototype.hasOwnProperty.call(GROUND_NODE, node.node_type)) {
    throw new Error(`Unknown node_type for frontend classification: ${String(node.node_type)}`);
  }
  return GROUND_NODE[node.node_type];
}

export function selectionTypeForNode(node: Pick<NodeState, "node_type">): "ground_station" | "satellite" {
  return isGroundNode(node) ? "ground_station" : "satellite";
}

export function isGroundLinkType(linkType: PublicLinkType): boolean {
  // A feed from another VS-API release can carry a kind this page does not know.
  if (!Object.prototype.hasOwnProperty.call(GROUND_LINK, linkType)) {
    throw new Error(`Unknown link_type for frontend link classification: ${String(linkType)}`);
  }
  return GROUND_LINK[linkType];
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
