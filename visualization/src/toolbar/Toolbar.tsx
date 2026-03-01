/** Left-edge vertical toolbar with icon buttons. */

import type { ViewMode, ColorMode } from "../types";

interface ToolbarProps {
  viewMode: ViewMode;
  colorMode: ColorMode;
  showGroundTracks: boolean;
  showAllLinks: boolean;
  onViewMode: (mode: ViewMode) => void;
  onColorMode: (mode: ColorMode) => void;
  onToggleGroundTracks: () => void;
  onToggleAllLinks: () => void;
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
  onViewMode,
  onColorMode,
  onToggleGroundTracks,
  onToggleAllLinks,
}: ToolbarProps) {
  return (
    <div className="toolbar">
      <ToolBtn label="Globe" icon="🌐" active={viewMode === "globe"} onClick={() => onViewMode("globe")} />
      <ToolBtn label="Topology" icon="◎" active={viewMode === "topology"} onClick={() => onViewMode("topology")} />
      <ToolBtn label="Split" icon="⬒" active={viewMode === "split"} onClick={() => onViewMode("split")} />
      <div className="toolbar-separator" />
      <ToolBtn
        label={colorMode === "area" ? "Color: Area" : "Color: Plane"}
        icon="◆"
        onClick={() => onColorMode(colorMode === "area" ? "plane" : "area")}
      />
      <ToolBtn
        label="Ground Tracks"
        icon="〰"
        active={showGroundTracks}
        onClick={onToggleGroundTracks}
      />
      <ToolBtn
        label="All Links"
        icon="⟷"
        active={showAllLinks}
        onClick={onToggleAllLinks}
      />
    </div>
  );
}
