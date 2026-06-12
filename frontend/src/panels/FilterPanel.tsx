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
  visibleSegments: Set<string> | null;
  onToggleSegment: (segmentId: string) => void;
  onShowAllSegments: () => void;
  onHideAllSegments: () => void;
  onFlyToSegment: (segmentId: string) => void;
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
  visibleSegments,
  onToggleSegment,
  onShowAllSegments,
  onHideAllSegments,
  onFlyToSegment,
}: FilterPanelProps) {
  const segments = useMemo(() => {
    if (!snapshot) return [];
    const bySegment = new Map<string, { id: string; count: number; tags: Set<string> }>();
    for (const node of snapshot.nodes) {
      const id = node.segment_id ?? "unsegmented";
      let segment = bySegment.get(id);
      if (!segment) {
        segment = { id, count: 0, tags: new Set<string>() };
        bySegment.set(id, segment);
      }
      segment.count += 1;
      for (const tag of node.tags ?? []) segment.tags.add(tag);
    }
    return [...bySegment.values()]
      .map((segment) => ({
        id: segment.id,
        count: segment.count,
        tags: [...segment.tags].sort(),
      }))
      .sort((a, b) => a.id.localeCompare(b.id));
  }, [snapshot]);

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
      {segments.length > 0 && (
        <div className="filter-section">
          <h3 className="filter-section-title">Segments</h3>
          <div className="filter-plane-actions">
            <button className="filter-quick-btn" onClick={onShowAllSegments}>All</button>
            <button className="filter-quick-btn" onClick={onHideAllSegments}>None</button>
          </div>
          <div className="filter-segment-list">
            {segments.map((segment) => {
              const isVisible = visibleSegments === null || visibleSegments.has(segment.id);
              return (
                <div key={segment.id} className="filter-segment-item">
                  <label className="filter-segment-toggle">
                    <input
                      className="ui-checkbox" type="checkbox"
                      checked={isVisible}
                      onChange={() => onToggleSegment(segment.id)}
                    />
                    <span className="filter-segment-main">
                      <span className="filter-segment-name">{segment.id}</span>
                      <span className="filter-segment-meta">
                        {segment.count} node{segment.count === 1 ? "" : "s"}
                        {segment.tags.length > 0 ? ` · ${segment.tags.join(", ")}` : ""}
                      </span>
                    </span>
                  </label>
                  <button
                    className="filter-fly-btn"
                    onClick={() => onFlyToSegment(segment.id)}
                    title={`Fly to ${segment.id}`}
                    aria-label={`Fly to segment ${segment.id}`}
                  >
                    Go
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

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
                  className="ui-checkbox" type="checkbox"
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
          <input className="ui-checkbox" type="checkbox" checked={showIslLinks} onChange={onToggleIslLinks} />
          <span>ISL Links</span>
        </label>
        <label className="filter-toggle-item">
          <input className="ui-checkbox" type="checkbox" checked={showGroundLinks} onChange={onToggleGroundLinks} />
          <span>Ground Links</span>
        </label>
        <label className="filter-toggle-item">
          <input className="ui-checkbox" type="checkbox" checked={showSatPaths} onChange={onToggleSatPaths} />
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
