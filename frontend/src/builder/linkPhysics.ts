// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** A display helper for backend-resolved terminal facts.
 *
 *  The resolved world already carries every node's terminal inventory
 *  (role, medium, elevation limits). This helper interprets those facts for
 *  display only; VS-API owns link creation and re-derivation.
 */

import type { BuilderWorldNode } from "./builderTypes";

/** The elevation floor a node's access beam is drawn with: the strictest
 *  floor its access terminals declare. No access terminal, no beam: null. */
export function accessBeamElevationDeg(node: BuilderWorldNode): number | null {
  const floors = node.terminal_inventory
    .filter((block) => block.endpoint_role === "access")
    .map((block) => block.min_elevation_deg);
  return floors.length === 0 ? null : Math.max(...floors);
}
