// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Hover tooltip — a DOM label following the cursor for the hovered satellite or ground
 * station. Rendered OUTSIDE the R3F
 * canvas (a fixed-position div) and fed by pointer-over handlers on the scene glyphs.
 * Content is React text, never HTML.
 */

import { Icon } from "../../ui/icons/Icon";
import { REGIME_TINT, type Regime } from "../../taxonomy/regime";
import type { NodeState } from "../../types";

export interface HoverInfo {
  node: NodeState;
  x: number;
  y: number;
  /** Canonical taxonomy sentence (family label + reason) — same words the inspector/logs use.
   *  When set it replaces the raw geometry line (the spec's "use the same taxonomy labels"). */
  caption?: string;
}

export function Tooltip({ hover, regime }: { hover: HoverInfo | null; regime?: Regime }) {
  if (!hover) return null;
  const n = hover.node;
  const isGround = n.node_type === "ground_station";
  const detail = hover.caption
    ? hover.caption
    : isGround
      ? `${n.lat_deg.toFixed(1)}°, ${n.lon_deg.toFixed(1)}°`
      : `${n.isl_count} ISLs, ${n.gnd_count} GND, Area ${n.routing_area ?? "none"}`;
  const tint = regime && regime !== "unknown" ? REGIME_TINT[regime] : null;
  return (
    <div className="scene-tooltip" style={{ left: hover.x + 12, top: hover.y - 8 }}>
      <span className="scene-tooltip-head">
        <Icon name={isGround ? "satellite-dish" : "satellite"} size={13} />
        <strong>{n.node_id}</strong>
        {tint && (
          <span className="scene-tooltip-regime">
            <span className="scene-tooltip-dot" style={{ background: tint.css }} aria-hidden="true" />
            {tint.label}
          </span>
        )}
      </span>
      {detail}
    </div>
  );
}
