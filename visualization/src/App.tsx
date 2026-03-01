/** Nodal Arc Visualization Frontend — main application component. */

import { useState, useCallback, useMemo, useRef } from "react";
import { GlobeView } from "./globe/GlobeView";
import { TopologyView } from "./topology/TopologyView";
import { InfoPanel } from "./panels/InfoPanel";
import { Toolbar } from "./toolbar/Toolbar";
import { TopBar } from "./bars/TopBar";
import { BottomBar } from "./bars/BottomBar";
import { TimeControls } from "./bars/TimeControls";
import { useSnapshot } from "./hooks/useSnapshot";
import { useSelection } from "./hooks/useSelection";
import { useKeyboard } from "./hooks/useKeyboard";
import type { ViewMode, ColorMode } from "./types";

import "./styles/variables.css";
import "./styles/reset.css";
import "./styles/layout.css";
import "./styles/panels.css";
import "./styles/toolbar.css";
import "./styles/topology.css";
import "./styles/time-controls.css";

export function App() {
  const { snapshot, connected, historicalMode, setHistoricalMode, fetchHistorical } =
    useSnapshot();
  const { selection, select, clearSelection } = useSelection();

  const [viewMode, setViewMode] = useState<ViewMode>("globe");
  const [colorMode, setColorMode] = useState<ColorMode>("area");
  const [showGroundTracks, setShowGroundTracks] = useState(false);
  const [showAllLinks, setShowAllLinks] = useState(true);
  const [followNode, setFollowNode] = useState(false);

  // Ref for GlobeView imperative actions (top view, follow, screenshot)
  const globeActionsRef = useRef<{
    flyToTopView: () => void;
    setFollowTarget: (nodeId: string | null) => void;
    captureScreenshot: () => void;
  } | null>(null);

  const toggleHistorical = useCallback(() => {
    setHistoricalMode(!historicalMode);
  }, [historicalMode, setHistoricalMode]);

  const toggleView = useCallback(() => {
    setViewMode((prev) => (prev === "globe" ? "topology" : "globe"));
  }, []);

  const handleFollowNode = useCallback(() => {
    if (!selection) return;
    setFollowNode((prev) => {
      const next = !prev;
      globeActionsRef.current?.setFollowTarget(next ? selection.id : null);
      return next;
    });
  }, [selection]);

  const handleTopView = useCallback(() => {
    globeActionsRef.current?.flyToTopView();
  }, []);

  const handleScreenshot = useCallback(() => {
    globeActionsRef.current?.captureScreenshot();
  }, []);

  const keyboardActions = useMemo(
    () => ({
      onEscape: clearSelection,
      onToggleView: toggleView,
      onSetColorMode: setColorMode,
      onToggleGroundTracks: () => setShowGroundTracks((v) => !v),
      onToggleAllLinks: () => setShowAllLinks((v) => !v),
      onToggleHistorical: toggleHistorical,
      onPlayPause: () => {}, // Handled by TimeControls
      onFollowNode: handleFollowNode,
      onTopView: handleTopView,
    }),
    [clearSelection, toggleView, toggleHistorical, handleFollowNode, handleTopView],
  );

  useKeyboard(keyboardActions);

  const layoutClass = `app-layout ${historicalMode ? "app-layout--historical" : ""}`;

  return (
    <div className={layoutClass}>
      <TopBar
        snapshot={snapshot}
        connected={connected}
        historicalMode={historicalMode}
        onToggleHistorical={toggleHistorical}
      />

      <div className={`area-viewport ${viewMode === "split" ? "area-viewport--split" : ""}`}>
        {!connected && (
          <div className="connection-banner">
            Connection lost. Reconnecting...
          </div>
        )}
        {(viewMode === "globe" || viewMode === "split") && (
          <div className={viewMode === "split" ? "split-pane" : "full-pane"}>
            <GlobeView
              snapshot={snapshot}
              selection={selection}
              onSelect={select}
              colorMode={colorMode}
              showGroundTracks={showGroundTracks}
              showAllLinks={showAllLinks}
              actionsRef={globeActionsRef}
              followNode={followNode}
            />
          </div>
        )}
        {(viewMode === "topology" || viewMode === "split") && (
          <div className={viewMode === "split" ? "split-pane" : "full-pane"}>
            <TopologyView
              snapshot={snapshot}
              selection={selection}
              onSelect={select}
            />
          </div>
        )}
        <Toolbar
          viewMode={viewMode}
          colorMode={colorMode}
          showGroundTracks={showGroundTracks}
          showAllLinks={showAllLinks}
          followNode={followNode}
          onViewMode={setViewMode}
          onColorMode={setColorMode}
          onToggleGroundTracks={() => setShowGroundTracks((v) => !v)}
          onToggleAllLinks={() => setShowAllLinks((v) => !v)}
          onTopView={handleTopView}
          onFollowNode={handleFollowNode}
          onScreenshot={handleScreenshot}
        />
      </div>

      <div className="area-panel">
        <InfoPanel snapshot={snapshot} selection={selection} onSelect={select} />
      </div>

      {historicalMode && (
        <TimeControls
          onSeek={fetchHistorical}
          startTime={snapshot?.sim_time ?? new Date().toISOString()}
          endTime={new Date().toISOString()}
        />
      )}

      <BottomBar snapshot={snapshot} connected={connected} />
    </div>
  );
}
