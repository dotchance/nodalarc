// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Draw topology links on Canvas 2D.
 *  Link colors come from the same tokens the globe materials use — the two
 *  views must never disagree about link semantics. Canvas cannot read CSS
 *  variables, so the stroke strings are precomputed from token values.
 */

import {
  FAIL_HOLD_MS,
  FAIL_FADE_MS,
  LINK_GROUND_COLOR,
  LINK_ISL_COLOR,
  LINK_FAIL_COLOR,
  TRACE_BRIDGED_COLOR,
  hexToCSS,
} from "../config";
import { withAlpha } from "../styles/tokens";
import type { TraceSegment } from "../trace/traceSegments";

const GROUND_STROKE = withAlpha(hexToCSS(LINK_GROUND_COLOR), 0.6);
const ISL_STROKE = withAlpha(hexToCSS(LINK_ISL_COLOR), 0.5);
const FLOW_BRIDGED_STROKE = hexToCSS(TRACE_BRIDGED_COLOR);

/** One traced leg to draw: its segments by the shared trace rule, the color of
 *  its direction, its opacity (below 1 while a stopped trace fades), and whether
 *  its dash flows. */
export interface FlowDrawing {
  segments: TraceSegment[];
  color: string;
  opacity: number;
  animate: boolean;
}
const FAIL_RGB = [(LINK_FAIL_COLOR >> 16) & 0xff, (LINK_FAIL_COLOR >> 8) & 0xff, LINK_FAIL_COLOR & 0xff] as const;
import type { LayoutLink, LayoutNode } from "./layout";

/** Point-to-line-segment distance for link hit testing. */
function pointToSegmentDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Hit test links — returns matching link key ("nodeA:nodeB") or null. */
export function hitTestLink(
  x: number,
  y: number,
  links: LayoutLink[],
  nodeMap: Map<string, LayoutNode>,
  threshold: number = 8,
): LayoutLink | null {
  for (const link of links) {
    const a = nodeMap.get(link.nodeA);
    const b = nodeMap.get(link.nodeB);
    if (!a || !b) continue;
    if (pointToSegmentDist(x, y, a.x, a.y, b.x, b.y) <= threshold) {
      return link;
    }
  }
  return null;
}

export function drawLinks(
  ctx: CanvasRenderingContext2D,
  links: LayoutLink[],
  nodeMap: Map<string, LayoutNode>,
  flows: readonly FlowDrawing[],
  dashOffset: number = 0,
  failTimes?: Map<string, number>,
  showIslLinks: boolean = true,
  showGroundLinks: boolean = true,
): void {
  const now = performance.now();
  const STUB_LEN = 20;

  // Precompute ring sizes for wrap detection
  const planeSlotsCount = new Map<number, number>();
  const planeSet = new Set<number>();
  for (const [, node] of nodeMap) {
    if (node.type === "satellite" && node.plane != null) {
      planeSlotsCount.set(node.plane, (planeSlotsCount.get(node.plane) ?? 0) + 1);
      planeSet.add(node.plane);
    }
  }
  const planeCount = planeSet.size;

  // Draw regular links
  for (const link of links) {
    const a = nodeMap.get(link.nodeA);
    const b = nodeMap.get(link.nodeB);
    if (!a || !b) continue;

    // Hide links based on toggle state (unless in fail-flash animation)
    if (link.state !== "failed") {
      if (link.isGround && !showGroundLinks) continue;
      if (!link.isGround && !showIslLinks) continue;
    }

    // Determine style before drawing
    let strokeStyle: string;
    let lineWidth: number;
    const dash: number[] = [];

    if (link.state === "failed") {
      const key = [link.nodeA, link.nodeB].sort().join(":");
      const ft = failTimes?.get(key) ?? now;
      const elapsed = now - ft;
      let opacity = 0.7;
      if (elapsed > FAIL_HOLD_MS) {
        opacity = 0.7 * (1 - (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS);
      }
      strokeStyle = `rgba(${FAIL_RGB[0]}, ${FAIL_RGB[1]}, ${FAIL_RGB[2]}, ${Math.max(0, opacity).toFixed(2)})`;
      lineWidth = 2;
    } else if (link.isGround) {
      strokeStyle = GROUND_STROKE;
      lineWidth = 2;
    } else {
      strokeStyle = ISL_STROKE;
      lineWidth = 1.5;
    }

    // Wrap detection for ISL links
    const isSat = a.type === "satellite" && b.type === "satellite";
    let isWrap = false;
    let wrapDir: "horizontal" | "vertical" = "horizontal";

    if (isSat && a.plane != null && b.plane != null && a.slot != null && b.slot != null) {
      // Intra-plane wrap: same plane, slot difference > half ring
      // Slots are on Y axis (transposed layout), so stubs go vertical
      if (a.plane === b.plane) {
        const ringSize = planeSlotsCount.get(a.plane) ?? 0;
        if (ringSize > 0 && Math.abs(a.slot - b.slot) > ringSize / 2) {
          isWrap = true;
          wrapDir = "vertical";
        }
      }
      // Cross-plane wrap: same slot, plane difference > half plane count
      // Planes are on X axis (transposed layout), so stubs go horizontal
      if (!isWrap && a.slot === b.slot && planeCount > 0) {
        if (Math.abs(a.plane - b.plane) > planeCount / 2) {
          isWrap = true;
          wrapDir = "horizontal";
        }
      }
    }

    ctx.strokeStyle = strokeStyle;
    ctx.lineWidth = lineWidth;
    ctx.setLineDash(dash);

    if (isWrap) {
      // Draw two short stubs from each endpoint toward the wrap edge
      if (wrapDir === "horizontal") {
        // Cross-plane wrap: planes on X axis
        const aDir = a.plane! < b.plane! ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x + aDir * STUB_LEN, a.y);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x - aDir * STUB_LEN, b.y);
        ctx.stroke();
      } else {
        // Intra-plane wrap: slots on Y axis
        const aDir = a.slot! < b.slot! ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x, a.y + aDir * STUB_LEN);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x, b.y - aDir * STUB_LEN);
        ctx.stroke();
      }
    } else {
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    ctx.setLineDash([]);
  }

  // Traced legs: measured segments dashed in the leg's color, bridged segments
  // dotted and thin in their own color.
  for (const flow of flows) {
    if (flow.segments.length === 0) continue;
    ctx.save();
    ctx.globalAlpha = flow.opacity;
    for (const segment of flow.segments) {
      const a = nodeMap.get(segment.from);
      const b = nodeMap.get(segment.to);
      if (!a || !b) continue;
      if (segment.measured) {
        ctx.strokeStyle = flow.color;
        ctx.lineWidth = 3;
        ctx.setLineDash([6, 3]);
        ctx.lineDashOffset = flow.animate ? -dashOffset : 0;
      } else {
        ctx.strokeStyle = FLOW_BRIDGED_STROKE;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([1, 5]);
        ctx.lineDashOffset = 0;
      }

      // Ring-wrap detection: same plane, slot difference > half ring
      const isRingWrap = a.type === "satellite" && b.type === "satellite"
        && a.plane != null && b.plane != null && a.plane === b.plane
        && a.slot != null && b.slot != null
        && (() => {
          const ringSize = planeSlotsCount.get(a.plane!) ?? 0;
          return ringSize > 0 && Math.abs(a.slot! - b.slot!) > ringSize / 2;
        })();

      if (isRingWrap) {
        // Draw two short vertical stubs (slots on Y axis in transposed layout)
        const aDir = a.slot! < b.slot! ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x, a.y + aDir * STUB_LEN);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x, b.y - aDir * STUB_LEN);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
    ctx.restore();
  }
}
