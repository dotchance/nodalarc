/** Left-edge vertical toolbar with icon buttons (VF spec Section 11). */

import type { ViewMode, ColorMode, GlobeMode } from "../types";

interface ToolbarProps {
  viewMode: ViewMode;
  colorMode: ColorMode;
  showGroundTracks: boolean;
  showAllLinks: boolean;
  followNode: boolean;
  canSplit: boolean;
  onViewMode: (mode: ViewMode) => void;
  onColorMode: (mode: ColorMode) => void;
  onToggleGroundTracks: () => void;
  onToggleAllLinks: () => void;
  globeMode: GlobeMode;
  onToggleGlobeMode: () => void;
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
  showGroundTracks,
  showAllLinks,
  followNode,
  canSplit,
  onViewMode,
  onColorMode,
  onToggleGroundTracks,
  onToggleAllLinks,
  globeMode,
  onToggleGlobeMode,
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
        label={`Ground Tracks: ${showGroundTracks ? "ON" : "OFF"} (G)`}
        icon="〰"
        active={showGroundTracks}
        onClick={onToggleGroundTracks}
      />
      <ToolBtn
        label={`All Links: ${showAllLinks ? "ON" : "OFF"} (L)`}
        icon="⟷"
        active={showAllLinks}
        onClick={onToggleAllLinks}
      />
      <ToolBtn
        label={`Globe: ${globeMode === "day-night" ? "Day/Night (N)" : "Blue Marble (N)"}`}
        icon="🌗"
        active={globeMode === "day-night"}
        onClick={onToggleGlobeMode}
      />
      <div className="toolbar-separator" />
      <ToolBtn label="Top View (T)" icon="⊙" onClick={onTopView} />
      <ToolBtn label="Follow Node (F)" icon="⊕" active={followNode} onClick={onFollowNode} />
      <ToolBtn label="Screenshot" icon="📷" onClick={onScreenshot} />
    </div>
  );
}
