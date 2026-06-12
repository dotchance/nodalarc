// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Left-edge vertical tool palette. Mode families (view, color, globe surface)
 * are Photoshop-style ToolSlots — click cycles, press-and-hold (or
 * right-click) opens the variant flyout. Booleans stay plain toggle buttons;
 * one-shot camera actions stay plain action buttons.
 */

import type { ViewMode, ColorMode, GlobeMode, ReferenceFrame } from "../types";
import { ToolSlot, type ToolVariant } from "../ui/ToolSlot";
import { Icon, type IconName } from "../ui/icons/Icon";

interface ToolbarProps {
  viewMode: ViewMode;
  colorMode: ColorMode;
  showGroundLinks: boolean;
  showIslLinks: boolean;
  showSatPaths: boolean;
  followNode: boolean;
  filterOpen: boolean;
  canSplit: boolean;
  referenceFrame: ReferenceFrame;
  onViewMode: (mode: ViewMode) => void;
  onColorMode: (mode: ColorMode) => void;
  onToggleGroundLinks: () => void;
  onToggleIslLinks: () => void;
  onToggleSatPaths: () => void;
  onToggleFilter: () => void;
  globeMode: GlobeMode;
  onGlobeMode: (mode: GlobeMode) => void;
  onToggleReferenceFrame: () => void;
  onTopView: () => void;
  onFrameScene: () => void;
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
  icon: IconName;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`toolbar-btn ${active ? "toolbar-btn--active" : ""}`}
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
    >
      <Icon name={icon} size={16} />
    </button>
  );
}

const GLOBE_MODE_VARIANTS: readonly ToolVariant<GlobeMode>[] = [
  { value: "blue-marble", label: "Blue Marble", icon: "earth", shortcut: "N" },
  { value: "day-night", label: "Day / Night", icon: "sun-moon", shortcut: "N" },
  { value: "political", label: "Political", icon: "map", shortcut: "N" },
];

const COLOR_MODE_VARIANTS: readonly ToolVariant<ColorMode>[] = [
  { value: "area", label: "Color by area", icon: "shapes", shortcut: "1" },
  { value: "plane", label: "Color by plane", icon: "layers", shortcut: "2" },
];

export function Toolbar({
  viewMode,
  colorMode,
  showGroundLinks,
  showIslLinks,
  showSatPaths,
  followNode,
  filterOpen,
  canSplit,
  referenceFrame,
  onViewMode,
  onColorMode,
  onToggleGroundLinks,
  onToggleIslLinks,
  onToggleSatPaths,
  onToggleFilter,
  globeMode,
  onGlobeMode,
  onToggleReferenceFrame,
  onTopView,
  onFrameScene,
  onFollowNode,
  onScreenshot,
}: ToolbarProps) {
  const viewVariants: ToolVariant<ViewMode>[] = [
    { value: "globe", label: "Globe", icon: "globe", shortcut: "Tab" },
    { value: "topology", label: "Topology", icon: "network", shortcut: "Tab" },
  ];
  if (canSplit) viewVariants.push({ value: "split", label: "Split", icon: "columns-2" });
  viewVariants.push({ value: "dashboard", label: "Dashboard", icon: "layout-dashboard" });

  return (
    <div className="toolbar">
      <ToolSlot label="View" variants={viewVariants} active={viewMode} onSelect={onViewMode} />
      <ToolSlot
        label="Globe surface"
        variants={GLOBE_MODE_VARIANTS}
        active={globeMode}
        onSelect={onGlobeMode}
      />
      <ToolSlot
        label="Node colors"
        variants={COLOR_MODE_VARIANTS}
        active={colorMode}
        onSelect={onColorMode}
      />
      <div className="toolbar-separator" />
      <ToolBtn
        label={`Ground links: ${showGroundLinks ? "on" : "off"} (G)`}
        icon="radio-tower"
        active={showGroundLinks}
        onClick={onToggleGroundLinks}
      />
      <ToolBtn
        label={`ISL links: ${showIslLinks ? "on" : "off"} (L)`}
        icon="spline"
        active={showIslLinks}
        onClick={onToggleIslLinks}
      />
      <ToolBtn
        label={`Orbital paths: ${showSatPaths ? "on" : "off"} (P)`}
        icon="orbit"
        active={showSatPaths}
        onClick={onToggleSatPaths}
      />
      <ToolBtn
        label="Segments / filters (Q)"
        icon="funnel"
        active={filterOpen}
        onClick={onToggleFilter}
      />
      <ToolBtn
        label={`Reference frame: ${referenceFrame === "earth-inertial" ? "Earth-inertial" : "Earth-fixed"} (I)`}
        icon="compass"
        active={referenceFrame === "earth-inertial"}
        onClick={onToggleReferenceFrame}
      />
      <div className="toolbar-separator" />
      <ToolBtn label="Top view (V)" icon="circle-arrow-up" onClick={onTopView} />
      <ToolBtn label="Frame scene (Home)" icon="frame" onClick={onFrameScene} />
      <ToolBtn label="Follow selection (Shift+F)" icon="locate-fixed" active={followNode} onClick={onFollowNode} />
      <ToolBtn label="Screenshot" icon="camera" onClick={onScreenshot} />
    </div>
  );
}
