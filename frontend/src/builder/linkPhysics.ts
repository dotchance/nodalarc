// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Display helpers for backend-resolved terminal facts.
 *
 *  The resolved world already carries every node's terminal inventory
 *  (role, medium, elevation limits). These helpers interpret those facts for
 *  display only; VS-API owns link creation and re-derivation.
 */

import type { BuilderWorld, BuilderWorldNode } from "./builderTypes";
import {
  type LinkMedium,
  type MountRole,
} from "./workspace";

export interface SegmentCapability {
  /** "role|medium" tokens present on at least one node of the segment. */
  pairs: Set<string>;
  /** Strictest access-terminal elevation floor seen on the segment. */
  access_min_elevation_deg: number | null;
}

/** The elevation floor a node's access beam is drawn with: the strictest
 *  floor its access terminals declare (the same reading as
 *  capabilitiesBySegment). No access terminal, no beam: null. */
export function accessBeamElevationDeg(node: BuilderWorldNode): number | null {
  const floors = node.terminal_inventory
    .filter((block) => block.endpoint_role === "access")
    .map((block) => block.min_elevation_deg);
  return floors.length === 0 ? null : Math.max(...floors);
}

/** Collect each segment's terminal capability from the resolved world. */
export function capabilitiesBySegment(
  world: BuilderWorld | null,
): Map<string, SegmentCapability> {
  const capabilities = new Map<string, SegmentCapability>();
  if (!world) return capabilities;
  for (const node of world.nodes) {
    let capability = capabilities.get(node.segment_id);
    if (!capability) {
      capability = { pairs: new Set(), access_min_elevation_deg: null };
      capabilities.set(node.segment_id, capability);
    }
    for (const block of node.terminal_inventory) {
      capability.pairs.add(`${block.endpoint_role}|${block.medium}`);
      if (block.endpoint_role === "access") {
        const floor = capability.access_min_elevation_deg;
        capability.access_min_elevation_deg =
          floor === null ? block.min_elevation_deg : Math.max(floor, block.min_elevation_deg);
      }
    }
  }
  return capabilities;
}

export type LinkRole = MountRole;

/** True when both endpoints carry at least one mount of role|medium. */
export function canForm(
  a: SegmentCapability | undefined,
  b: SegmentCapability | undefined,
  role: LinkRole,
  medium: LinkMedium,
): boolean {
  const token = `${role}|${medium}`;
  return Boolean(a?.pairs.has(token) && b?.pairs.has(token));
}
