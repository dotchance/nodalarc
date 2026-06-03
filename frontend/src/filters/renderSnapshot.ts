// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Pure render-snapshot filtering for UI controls.
 *
 * This does not mutate or replace the authoritative snapshot. It produces the
 * view subset passed to renderers while details/logs continue to read full
 * truth from VS-API.
 */

import type { NodeState, StateSnapshot } from "../types";

export function nodeSegmentId(node: NodeState): string {
  return node.segment_id ?? "unsegmented";
}

export function filterSnapshotForRender(
  snapshot: StateSnapshot | null,
  visibleSegments: Set<string> | null,
  visiblePlanes: Set<number> | null,
): StateSnapshot | null {
  if (!snapshot) return snapshot;
  if (visibleSegments === null && visiblePlanes === null) return snapshot;

  const nodes = snapshot.nodes.filter((node) => {
    if (visibleSegments !== null && !visibleSegments.has(nodeSegmentId(node))) return false;
    if (
      visiblePlanes !== null &&
      node.node_type === "satellite" &&
      node.plane != null &&
      !visiblePlanes.has(node.plane)
    ) {
      return false;
    }
    return true;
  });
  const visibleNodeIds = new Set(nodes.map((node) => node.node_id));
  const links = snapshot.links.filter(
    (link) => visibleNodeIds.has(link.node_a) && visibleNodeIds.has(link.node_b),
  );
  const kernelActualPairs = snapshot.kernel_actual_pairs?.filter(
    ([a, b]) => visibleNodeIds.has(a) && visibleNodeIds.has(b),
  );
  const tracedPaths = snapshot.traced_paths.filter((path) => {
    const reverse = path.reverse_hops ?? [];
    return (
      path.hops.every((nodeId) => visibleNodeIds.has(nodeId)) &&
      reverse.every((nodeId) => visibleNodeIds.has(nodeId))
    );
  });
  const activeFlows = snapshot.active_flows.filter(
    (flow) => visibleNodeIds.has(flow.src_node) && visibleNodeIds.has(flow.dst_node),
  );

  return {
    ...snapshot,
    nodes,
    links,
    kernel_actual_pairs: kernelActualPairs,
    traced_paths: tracedPaths,
    active_flows: activeFlows,
  };
}
