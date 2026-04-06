// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Left-edge vertical toolbar with icon buttons (VF spec Section 11). */

import type { ViewMode, ColorMode, GlobeMode, ReferenceFrame } from "../types";

interface ToolbarProps {
  viewMode: ViewMode;
  colorMode: ColorMode;
  showGroundLinks: boolean;
  showIslLinks: boolean;
  showSatPaths: boolean;
  followNode: boolean;
  canSplit: boolean;
  referenceFrame: ReferenceFrame;
  onViewMode: (mode: ViewMode) => void;
  onColorMode: (mode: ColorMode) => void;
  onToggleGroundLinks: () => void;
  onToggleIslLinks: () => void;
  onToggleSatPaths: () => void;
  globeMode: GlobeMode;
  onToggleGlobeMode: () => void;
  onToggleReferenceFrame: () => void;
  onTopView: () => void;
  onFollowNode: () => void;
  onScreenshot: () => void;
}

function ToolBtn({
  label,
  icon,
  active,
  onClick,
}: {
  label: string;
  icon: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`toolbar-btn ${active ? "toolbar-btn--active" : ""}`}
      onClick={onClick}
      title={label}
    >
      {icon}
      <span className="toolbar-tooltip">{label}</span>
    </button>
  );
}

export function Toolbar({
  viewMode,
  colorMode,
  showGroundLinks,
  showIslLinks,
  showSatPaths,
  followNode,
  canSplit,
  referenceFrame,
  onViewMode,
  onColorMode,
  onToggleGroundLinks,
  onToggleIslLinks,
  onToggleSatPaths,
  globeMode,
  onToggleGlobeMode,
  onToggleReferenceFrame,
  onTopView,
  onFollowNode,
  onScreenshot,
}: ToolbarProps) {
  return (
    <div className="toolbar">
      <ToolBtn label="Globe (Tab)" icon="🌐" active={viewMode === "globe"} onClick={() => onViewMode("globe")} />
      <ToolBtn label="Topology (Tab)" icon="◎" active={viewMode === "topology"} onClick={() => onViewMode("topology")} />
      {canSplit && (
        <ToolBtn label="Split" icon="⬒" active={viewMode === "split"} onClick={() => onViewMode("split")} />
      )}
      <div className="toolbar-separator" />
      <ToolBtn
        label={`Color: ${colorMode === "area" ? "Area (1)" : "Plane (2)"}`}
        icon="◆"
        onClick={() => onColorMode(colorMode === "area" ? "plane" : "area")}
      />
      <ToolBtn
        label={`Ground Links: ${showGroundLinks ? "ON" : "OFF"} (G)`}
        icon="〰"
        active={showGroundLinks}
        onClick={onToggleGroundLinks}
      />
      <ToolBtn
        label={`ISL Links: ${showIslLinks ? "ON" : "OFF"} (L)`}
        icon="⟷"
        active={showIslLinks}
        onClick={onToggleIslLinks}
      />
      <ToolBtn
        label={`Satellite Paths: ${showSatPaths ? "ON" : "OFF"} (P)`}
        icon="⭕"
        active={showSatPaths}
        onClick={onToggleSatPaths}
      />
      <ToolBtn
        label={`Globe: ${globeMode === "day-night" ? "Day/Night (N)" : "Blue Marble (N)"}`}
        icon="🌗"
        active={globeMode === "day-night"}
        onClick={onToggleGlobeMode}
      />
      <ToolBtn
        label={`Frame: ${referenceFrame === "earth-inertial" ? "Earth-Inertial (I)" : "Earth-Fixed (I)"}`}
        icon="🌀"
        active={referenceFrame === "earth-inertial"}
        onClick={onToggleReferenceFrame}
      />
      <div className="toolbar-separator" />
      <ToolBtn label="Top View (T)" icon="⊙" onClick={onTopView} />
      <ToolBtn label="Follow Node (F)" icon="⊕" active={followNode} onClick={onFollowNode} />
      <ToolBtn label="Screenshot" icon="📷" onClick={onScreenshot} />
    </div>
  );
}
