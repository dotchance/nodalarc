/** Nodal Arc Visualization Frontend — main application component. */

import { useState, useCallback, useMemo } from "react";
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

  const toggleHistorical = useCallback(() => {
    setHistoricalMode(!historicalMode);
  }, [historicalMode, setHistoricalMode]);

  const keyboardActions = useMemo(
    () => ({
      onEscape: clearSelection,
      onToggleView: setViewMode,
      onToggleColorMode: setColorMode,
      onToggleGroundTracks: () => setShowGroundTracks((v) => !v),
      onToggleAllLinks: () => setShowAllLinks((v) => !v),
      onToggleHistorical: toggleHistorical,
      onPlayPause: () => {}, // Handled by TimeControls
    }),
    [clearSelection, toggleHistorical],
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

      <div className="area-viewport">
        {(viewMode === "globe" || viewMode === "split") && (
          <GlobeView
            snapshot={snapshot}
            selection={selection}
            onSelect={select}
            colorMode={colorMode}
            showGroundTracks={showGroundTracks}
            showAllLinks={showAllLinks}
          />
        )}
        {viewMode === "topology" && (
          <TopologyView
            snapshot={snapshot}
            selection={selection}
            onSelect={select}
          />
        )}
        <Toolbar
          viewMode={viewMode}
          colorMode={colorMode}
          showGroundTracks={showGroundTracks}
          showAllLinks={showAllLinks}
          onViewMode={setViewMode}
          onColorMode={setColorMode}
          onToggleGroundTracks={() => setShowGroundTracks((v) => !v)}
          onToggleAllLinks={() => setShowAllLinks((v) => !v)}
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
