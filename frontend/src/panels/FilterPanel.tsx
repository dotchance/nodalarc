// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Filter panel — platform filtering and overlay controls.
//
// Provides per-orbital-plane toggles, node type filters, and link
// visibility controls. Lives in the left zone of the layout shell.

import { useMemo } from "react";
import { getPlaneColor } from "../config";
import type { StateSnapshot, ColorMode } from "../types";

interface FilterPanelProps {
  snapshot: StateSnapshot | null;
  showIslLinks: boolean;
  showGroundLinks: boolean;
  showSatPaths: boolean;
  colorMode: ColorMode;
  onToggleIslLinks: () => void;
  onToggleGroundLinks: () => void;
  onToggleSatPaths: () => void;
  onSetColorMode: (mode: ColorMode) => void;
  visiblePlanes: Set<number> | null;
  onTogglePlane: (plane: number) => void;
  onShowAllPlanes: () => void;
  onHideAllPlanes: () => void;
}

function hexToCSS(hex: number): string {
  return "#" + hex.toString(16).padStart(6, "0");
}

export function FilterPanel({
  snapshot,
  showIslLinks,
  showGroundLinks,
  showSatPaths,
  colorMode,
  onToggleIslLinks,
  onToggleGroundLinks,
  onToggleSatPaths,
  onSetColorMode,
  visiblePlanes,
  onTogglePlane,
  onShowAllPlanes,
  onHideAllPlanes,
}: FilterPanelProps) {
  const planes = useMemo(() => {
    if (!snapshot) return [];
    const planeSet = new Set<number>();
    for (const node of snapshot.nodes) {
      if (node.node_type === "satellite" && node.plane != null) {
        planeSet.add(node.plane);
      }
    }
    return [...planeSet].sort((a, b) => a - b);
  }, [snapshot]);

  return (
    <div className="filter-panel">
      <div className="filter-section">
        <h3 className="filter-section-title">Platforms</h3>
        <div className="filter-plane-actions">
          <button className="filter-quick-btn" onClick={onShowAllPlanes}>All</button>
          <button className="filter-quick-btn" onClick={onHideAllPlanes}>None</button>
        </div>
        <div className="filter-plane-list">
          {planes.map((plane) => {
            const color = getPlaneColor(plane);
            const isVisible = visiblePlanes === null || visiblePlanes.has(plane);
            return (
              <label key={plane} className="filter-plane-item">
                <input
                  type="checkbox"
                  checked={isVisible}
                  onChange={() => onTogglePlane(plane)}
                />
                <span
                  className="filter-plane-swatch"
                  style={{ background: hexToCSS(color) }}
                />
                <span className="filter-plane-label">Plane {plane}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="filter-section">
        <h3 className="filter-section-title">Overlays</h3>
        <label className="filter-toggle-item">
          <input type="checkbox" checked={showIslLinks} onChange={onToggleIslLinks} />
          <span>ISL Links</span>
        </label>
        <label className="filter-toggle-item">
          <input type="checkbox" checked={showGroundLinks} onChange={onToggleGroundLinks} />
          <span>Ground Links</span>
        </label>
        <label className="filter-toggle-item">
          <input type="checkbox" checked={showSatPaths} onChange={onToggleSatPaths} />
          <span>Orbital Paths</span>
        </label>
      </div>

      <div className="filter-section">
        <h3 className="filter-section-title">Color Mode</h3>
        <div className="filter-color-modes">
          {(["area", "plane"] as const).map((mode) => (
            <button
              key={mode}
              className={`filter-color-btn ${colorMode === mode ? "filter-color-btn--active" : ""}`}
              onClick={() => onSetColorMode(mode)}
            >
              {mode === "area" ? "Routing Area" : "Orbital Plane"}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
