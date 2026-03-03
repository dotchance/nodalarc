/** Nodal Arc Visualization Frontend — main application component. */

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { GlobeView } from "./globe/GlobeView";
import { TopologyView } from "./topology/TopologyView";
import { InfoPanel } from "./panels/InfoPanel";
import { CliDrawer } from "./panels/CliDrawer";
import { NodePopover } from "./panels/NodePopover";
import { Toolbar } from "./toolbar/Toolbar";
import { TopBar } from "./bars/TopBar";
import { BottomBar } from "./bars/BottomBar";
import { TimeControls } from "./bars/TimeControls";
import { useSnapshot } from "./hooks/useSnapshot";
import { useSelection } from "./hooks/useSelection";
import { useKeyboard } from "./hooks/useKeyboard";
import { useSessionSwitcher } from "./hooks/useSessionSwitcher";
import { WS_URL } from "./config";
import type { ViewMode, ColorMode } from "./types";

import "./styles/variables.css";
import "./styles/reset.css";
import "./styles/layout.css";
import "./styles/panels.css";
import "./styles/toolbar.css";
import "./styles/topology.css";
import "./styles/time-controls.css";

export function App() {
  const { snapshot, connected, hasEverConnected, historicalMode, setHistoricalMode, fetchHistorical } =
    useSnapshot();
  const { selection, select, clearSelection } = useSelection();
  const { sessions, switching, switchSession } = useSessionSwitcher(snapshot?.session_status ?? null);

  const [viewMode, setViewMode] = useState<ViewMode>("globe");
  const [colorMode, setColorMode] = useState<ColorMode>("area");
  const [showGroundTracks, setShowGroundTracks] = useState(false);
  const [showAllLinks, setShowAllLinks] = useState(true);
  const [followNode, setFollowNode] = useState(false);
  const [windowWidth, setWindowWidth] = useState(window.innerWidth);
  const [historicalPlaying, setHistoricalPlaying] = useState(false);
  const playingRef = useRef(historicalPlaying);

  // Track window width for split view gate
  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const canSplit = windowWidth >= 1920;

  const [cliDrawerOpen, setCliDrawerOpen] = useState(false);

  // Ref for GlobeView imperative actions (top view, follow, screenshot, flyTo, screen position)
  const globeActionsRef = useRef<{
    flyToTopView: () => void;
    setFollowTarget: (nodeId: string | null) => void;
    captureScreenshot: () => void;
    flyToNode: (nodeId: string) => void;
    getNodeScreenPosition: (nodeId: string) => { x: number; y: number; visible: boolean } | null;
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

  const handleFlyToNode = useCallback((nodeId: string) => {
    globeActionsRef.current?.flyToNode(nodeId);
  }, []);

  const keyboardActions = useMemo(
    () => ({
      onEscape: clearSelection,
      onToggleView: toggleView,
      onSetColorMode: setColorMode,
      onToggleGroundTracks: () => setShowGroundTracks((v) => !v),
      onToggleAllLinks: () => setShowAllLinks((v) => !v),
      onToggleHistorical: toggleHistorical,
      onPlayPause: () => {
        if (historicalMode) {
          setHistoricalPlaying((prev) => {
            playingRef.current = !prev;
            return !prev;
          });
        }
      },
      onFollowNode: handleFollowNode,
      onTopView: handleTopView,
      onToggleCli: () => setCliDrawerOpen((v) => !v),
    }),
    [clearSelection, toggleView, toggleHistorical, handleFollowNode, handleTopView, historicalMode],
  );

  useKeyboard(keyboardActions);

  // When switching back to globe with an active selection, fly to that node
  const prevViewModeRef = useRef(viewMode);
  useEffect(() => {
    const prev = prevViewModeRef.current;
    prevViewModeRef.current = viewMode;
    if (viewMode !== prev && (viewMode === "globe" || viewMode === "split") && selection) {
      globeActionsRef.current?.flyToNode(selection.id);
    }
  }, [viewMode, selection]);

  const layoutClass = `app-layout ${historicalMode ? "app-layout--historical" : ""}`;

  return (
    <div className={layoutClass}>
      <TopBar
        snapshot={snapshot}
        connected={connected}
        historicalMode={historicalMode}
        onToggleHistorical={toggleHistorical}
        sessions={sessions}
        switching={switching}
        onSwitchSession={switchSession}
      />

      <div className={`area-viewport ${viewMode === "split" ? "area-viewport--split" : ""}`}>
        {!connected && !hasEverConnected && (
          <div className="connection-startup-error">
            <div className="startup-error-box">
              <h2>Cannot connect to VS-API</h2>
              <p>Endpoint: {snapshot ? "" : WS_URL}</p>
              <button onClick={() => window.location.reload()}>Retry</button>
            </div>
          </div>
        )}
        {!connected && hasEverConnected && (
          <div className="connection-banner">
            Connection lost. Reconnecting...
          </div>
        )}
        {switching && (
          <div className="session-switching-overlay">
            <div className="switching-box">
              <p>Switching session...</p>
              <p style={{ fontSize: 10, color: "var(--text-dim)" }}>
                {snapshot?.session_status_detail ?? ""}
              </p>
            </div>
          </div>
        )}
        <div
          className={viewMode === "split" ? "split-pane" : "full-pane"}
          style={{ display: viewMode === "topology" ? "none" : undefined }}
        >
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
        <div
          className={viewMode === "split" ? "split-pane" : "full-pane"}
          style={{ display: viewMode === "globe" ? "none" : undefined }}
        >
          <TopologyView
            snapshot={snapshot}
            selection={selection}
            onSelect={select}
            onFlyTo={handleFlyToNode}
          />
        </div>
        <Toolbar
          viewMode={viewMode}
          colorMode={colorMode}
          showGroundTracks={showGroundTracks}
          showAllLinks={showAllLinks}
          followNode={followNode}
          canSplit={canSplit}
          onViewMode={setViewMode}
          onColorMode={setColorMode}
          onToggleGroundTracks={() => setShowGroundTracks((v) => !v)}
          onToggleAllLinks={() => setShowAllLinks((v) => !v)}
          onTopView={handleTopView}
          onFollowNode={handleFollowNode}
          onScreenshot={handleScreenshot}
        />
        {viewMode !== "topology" && selection?.type !== "link" && selection != null && !cliDrawerOpen && (
          <NodePopover
            snapshot={snapshot}
            selection={selection}
            onClose={clearSelection}
            onOpenCli={() => setCliDrawerOpen(true)}
            globeActionsRef={globeActionsRef}
          />
        )}
        {cliDrawerOpen && (
          <CliDrawer
            open={cliDrawerOpen}
            onClose={() => setCliDrawerOpen(false)}
            snapshot={snapshot}
            selection={selection}
          />
        )}
      </div>

      <div className="area-panel">
        <InfoPanel snapshot={snapshot} selection={selection} onSelect={select} onFlyTo={handleFlyToNode} />
      </div>

      {historicalMode && (
        <TimeControls
          onSeek={fetchHistorical}
          startTime={snapshot?.sim_time ?? new Date().toISOString()}
          endTime={new Date().toISOString()}
          events={snapshot?.recent_events}
          externalPlaying={historicalPlaying}
          onPlayingChange={setHistoricalPlaying}
        />
      )}

      <BottomBar snapshot={snapshot} connected={connected} historicalMode={historicalMode} />
    </div>
  );
}
