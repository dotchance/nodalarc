// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Layout shell — four-zone architecture for the VF.
//
// Defines the structural container where UI elements live:
// - TopBar zone (fixed height)
// - Left panel zone (collapsible, filter/overlay controls)
// - Center zone (Globe or Topology view)
// - Right panel zone (appears on selection, detail + CLI)
// - BottomBar zone (fixed height)
//
// Responsive behavior:
// - Desktop (≥1280px): side panels inline
// - Tablet (768-1279px): globe full-width, panels as overlays
//
// Content is injected via children props — the Shell owns layout,
// not content.

import { useCallback, useRef, type ReactNode } from "react";

interface ShellProps {
  topBar: ReactNode;
  center: ReactNode;
  rightPanel: ReactNode | null;
  bottomBar: ReactNode;
  overlay?: ReactNode;
  toasts?: ReactNode;
  historicalControls?: ReactNode;
  historicalMode?: boolean;
  centerSplit?: boolean;
  panelOpen: boolean;
  onPanelToggle: () => void;
  panelWidth: number;
  onPanelWidthChange: (width: number) => void;
}

export function Shell({
  topBar,
  center,
  rightPanel,
  bottomBar,
  overlay,
  toasts,
  historicalControls,
  historicalMode,
  centerSplit = false,
  panelOpen,
  onPanelToggle,
  panelWidth,
  onPanelWidthChange,
}: ShellProps) {
  const panelDraggingRef = useRef(false);
  // Latest dragged width — the mouseup closure must not capture the stale
  // render-time prop, or we persist the pre-drag width.
  const latestWidthRef = useRef(panelWidth);
  latestWidthRef.current = panelWidth;

  const handlePanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    panelDraggingRef.current = true;
    const onMove = (ev: MouseEvent) => {
      if (!panelDraggingRef.current) return;
      const newWidth = Math.max(280, Math.min(800, window.innerWidth - ev.clientX));
      latestWidthRef.current = newWidth;
      onPanelWidthChange(newWidth);
    };
    const onUp = () => {
      panelDraggingRef.current = false;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      localStorage.setItem("nodal_panel_width", String(latestWidthRef.current));
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, [onPanelWidthChange]);

  const layoutClass = `app-layout${panelOpen ? " app-layout--panel-open" : ""}${historicalMode ? " app-layout--historical" : ""}`;

  return (
    <div className={layoutClass} style={{ "--panel-width": `${panelWidth}px` } as React.CSSProperties}>
      {topBar}

      {overlay}
      {toasts}

      <div className={`area-viewport${centerSplit ? " area-viewport--split" : ""}`}>
        {center}
        <button
          className="panel-toggle-tab"
          onClick={onPanelToggle}
          title={panelOpen ? "Collapse panel" : "Expand panel"}
        >
          {panelOpen ? "›" : "‹"}
        </button>
      </div>

      <div className="area-panel">
        <div className="panel-resize-handle" onMouseDown={handlePanelResizeStart} />
        {rightPanel}
      </div>

      {historicalMode && historicalControls}

      {bottomBar}
    </div>
  );
}
