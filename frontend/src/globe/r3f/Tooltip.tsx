// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Hover tooltip — a DOM label following the cursor for the hovered satellite or ground
 * station, reproducing the globe/gpuPicker.ts tooltip content. Rendered OUTSIDE the R3F
 * canvas (a fixed-position div) and fed by pointer-over handlers on the scene glyphs.
 * Content is React text (never HTML) — XSS-safe, matching the legacy textContent tooltip.
 */

import type { NodeState } from "../../types";

export interface HoverInfo {
  node: NodeState;
  x: number;
  y: number;
  /** Canonical taxonomy sentence (family label + reason) — same words the inspector/logs use.
   *  When set it replaces the raw geometry line (the spec's "use the same taxonomy labels"). */
  caption?: string;
}

export function Tooltip({ hover }: { hover: HoverInfo | null }) {
  if (!hover) return null;
  const n = hover.node;
  const text = hover.caption
    ? `${n.node_id}: ${hover.caption}`
    : n.node_type === "satellite"
      ? `${n.node_id}\n${n.isl_count} ISLs, ${n.gnd_count} GND, Area ${n.routing_area ?? "none"}`
      : `${n.node_id}\n${n.lat_deg.toFixed(1)}°, ${n.lon_deg.toFixed(1)}°`;
  return (
    <div
      style={{
        position: "fixed",
        left: hover.x + 12,
        top: hover.y - 8,
        pointerEvents: "none",
        whiteSpace: "pre-line",
        zIndex: 20,
        padding: "4px 8px",
        borderRadius: 4,
        font: "11px/1.4 monospace",
        color: "var(--text-primary, #e0e0e0)",
        background: "var(--bg-scrim, rgba(13,13,26,0.9))",
        border: "1px solid rgba(255,255,255,0.15)",
      }}
    >
      {text}
    </div>
  );
}
