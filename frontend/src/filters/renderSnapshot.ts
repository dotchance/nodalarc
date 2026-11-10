// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
/** Pure render-snapshot filtering for UI controls.
 *
 * This does not mutate or replace the authoritative snapshot. It produces the
 * view subset passed to renderers while details/logs continue to read full
 * truth from VS-API.
 */

import type { StateSnapshot } from "../generated/vsApi";

export function filterSnapshotForRender(
  snapshot: StateSnapshot | null,
  visibleSegments: Set<string> | null,
  visiblePlanes: Set<number> | null,
): StateSnapshot | null {
  if (!snapshot) return snapshot;
  if (visibleSegments === null && visiblePlanes === null) return snapshot;

  const nodes = snapshot.nodes.filter((node) => {
    if (visibleSegments !== null && !visibleSegments.has(node.segment_id)) return false;
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
  const kernelActualPairs = snapshot.kernel_actual_pairs.filter(
    ([a, b]) => visibleNodeIds.has(a) && visibleNodeIds.has(b),
  );

  return {
    ...snapshot,
    nodes,
    links,
    kernel_actual_pairs: kernelActualPairs,
    // Traces are drawn whole whatever the filters show: a hop on a hidden node is
    // bridged like any other hop the view cannot place.
  };
}
