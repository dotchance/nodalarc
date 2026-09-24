// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** The one rule for drawing a traced path, shared by the globe and the topology view.
 *
 *  A trace lists what answered at each hop: a node, an address no node owns,
 *  or `*` when nothing answered. A segment between two consecutive hops that
 *  can both be placed on screen was measured. Where hops between two placed
 *  hops cannot be placed (a silent hop, an unowned address, a node the view
 *  does not show), a bridged segment joins the placed hops on either side and
 *  is drawn in its own style. Hops after the last placed hop draw nothing.
 */

export interface TraceSegment {
  from: string;
  to: string;
  /** True when the two hops are consecutive in the trace; false when it bridges hops
   *  that could not be placed. */
  measured: boolean;
}

export function traceSegments(
  hops: readonly string[],
  placeable: (hop: string) => boolean,
): TraceSegment[] {
  const segments: TraceSegment[] = [];
  let lastIndex: number | null = null;
  hops.forEach((hop, index) => {
    if (!placeable(hop)) return;
    if (lastIndex !== null) {
      segments.push({ from: hops[lastIndex]!, to: hop, measured: index === lastIndex + 1 });
    }
    lastIndex = index;
  });
  return segments;
}
