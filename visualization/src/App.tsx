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
import { usePlayback } from "./hooks/usePlayback";
import { useManifest } from "./hooks/useManifest";
import { SessionCatalog } from "./catalog/SessionCatalog";
import { WS_URL, fetchApiKey } from "./config";
import type { ViewMode, ColorMode, GlobeMode } from "./types";

import "./styles/variables.css";
import "./styles/reset.css";
import "./styles/layout.css";
import "./styles/panels.css";
import "./styles/toolbar.css";
import "./styles/topology.css";
import "./styles/time-controls.css";
import "./styles/catalog.css";

export function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => { fetchApiKey().finally(() => setReady(true)); }, []);
  if (!ready) return null;
  return <AppInner />;
}

function AppInner() {
  const { snapshot, connected, hasEverConnected, historicalMode, setHistoricalMode, fetchHistorical } =
    useSnapshot();
  const { selection, select, clearSelection } = useSelection();

  // On initial mount, check for ?selected=<node_id> deep-link from NodalPath console
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const preselected = params.get("selected");
    if (preselected) {
      const type = preselected.startsWith("gs-") ? "ground_station" as const : "satellite" as const;
      select({ type, id: preselected });
    }
  }, [select]);

  const { sessions, switching, switchSession } = useSessionSwitcher(snapshot?.session_status ?? null);
  const playback = usePlayback(snapshot?.playback_paused, snapshot?.playback_speed);
  const { manifest } = useManifest();

  const [showCatalog, setShowCatalog] = useState(true);
  const [hasEverDeployed, setHasEverDeployed] = useState(false);

  const activeSession = sessions.find((s) => s.active);
  const activeSessionId = activeSession
    ? activeSession.file.replace("configs/sessions/", "").replace(".yaml", "")
    : null;
  const activeSessionName = activeSession?.name ?? null;

  // If VS-API already has an active session (e.g. after HMR reload), allow closing catalog
  useEffect(() => {
    if (activeSessionId && !hasEverDeployed) {
      setHasEverDeployed(true);
    }
  }, [activeSessionId, hasEverDeployed]);

  const handleDeploy = useCallback((sessionId: string) => {
    // If clicking the already-running session, just close the catalog
    if (sessionId === activeSessionId) {
      setShowCatalog(false);
      return;
    }
    switchSession(`configs/sessions/${sessionId}.yaml`);
    setShowCatalog(false);
    setHasEverDeployed(true);
  }, [switchSession, activeSessionId]);

  const [viewMode, setViewMode] = useState<ViewMode>("globe");
  const [colorMode, setColorMode] = useState<ColorMode>("area");
  const [showGroundTracks, setShowGroundTracks] = useState(false);
  const [showAllLinks, setShowAllLinks] = useState(true);
  const [globeMode, setGlobeMode] = useState<GlobeMode>("blue-marble");
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

  // Track whether sim_time has advanced since nodes appeared — detect "frozen" globe
  const firstSimTimeRef = useRef<string | null>(null);
  const [simTimeAdvanced, setSimTimeAdvanced] = useState(false);

  useEffect(() => {
    if (switching || !snapshot || snapshot.nodes.length === 0) {
      // Reset when constellation clears or session switch starts
      firstSimTimeRef.current = null;
      setSimTimeAdvanced(false);
      return;
    }
    if (firstSimTimeRef.current === null) {
      firstSimTimeRef.current = snapshot.sim_time;
      return;
    }
    if (!simTimeAdvanced && snapshot.sim_time !== firstSimTimeRef.current) {
      setSimTimeAdvanced(true);
    }
  }, [snapshot, switching, simTimeAdvanced]);

  const [cliDrawerOpen, setCliDrawerOpen] = useState(false);

  // Panel collapse/expand state
  const [panelOpen, setPanelOpen] = useState(false);
  const panelManualRef = useRef(false);

  // Auto-open panel on selection, auto-close on deselect (unless manually toggled)
  useEffect(() => {
    if (selection && !panelManualRef.current) {
      setPanelOpen(true);
    } else if (!selection && !panelManualRef.current) {
      setPanelOpen(false);
    }
  }, [selection]);

  // Reset manual override when a new selection is made
  useEffect(() => {
    if (selection) {
      panelManualRef.current = false;
    }
  }, [selection]);

  const handlePanelToggle = useCallback(() => {
    setPanelOpen((v) => !v);
    panelManualRef.current = true;
  }, []);

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

  const handleCloseCatalog = useCallback(() => {
    if (showCatalog && hasEverDeployed) {
      setShowCatalog(false);
    }
  }, [showCatalog, hasEverDeployed]);

  const keyboardActions = useMemo(
    () => ({
      onEscape: clearSelection,
      onCloseCatalog: showCatalog && hasEverDeployed ? handleCloseCatalog : undefined,
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
        } else {
          if (playback.paused) {
            playback.resume();
          } else {
            playback.pause();
          }
        }
      },
      onToggleGlobeMode: () => setGlobeMode((m) => m === "blue-marble" ? "day-night" : "blue-marble"),
      onFollowNode: handleFollowNode,
      onTopView: handleTopView,
      onToggleCli: () => setCliDrawerOpen((v) => !v),
      onTogglePanel: handlePanelToggle,
    }),
    [clearSelection, handleCloseCatalog, showCatalog, hasEverDeployed, toggleView, toggleHistorical, handleFollowNode, handleTopView, historicalMode, playback, handlePanelToggle],
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

  const layoutClass = `app-layout${panelOpen ? " app-layout--panel-open" : ""}${historicalMode ? " app-layout--historical" : ""}`;

  return (
    <div className={layoutClass}>
      <TopBar
        snapshot={snapshot}
        connected={connected}
        historicalMode={historicalMode}
        onToggleHistorical={toggleHistorical}
        activeSessionName={activeSessionName}
        switching={switching}
        onOpenCatalog={() => setShowCatalog(true)}
        playbackPaused={playback.paused}
        playbackSpeed={playback.speed}
        playbackLoading={playback.loading}
        onPlaybackPause={playback.pause}
        onPlaybackResume={playback.resume}
        onPlaybackSetSpeed={playback.setSpeed}
      />

      {showCatalog && (
        <SessionCatalog
          manifest={manifest}
          activeSessionId={activeSessionId}
          onDeploy={handleDeploy}
          onClose={hasEverDeployed ? () => setShowCatalog(false) : undefined}
          deploying={switching}
          fallbackSessions={sessions}
        />
      )}

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
        {connected && !switching && (!snapshot || snapshot.nodes.length === 0) && (
          <div className="connection-banner">
            Initializing constellation...
          </div>
        )}
        {connected && !switching && snapshot && snapshot.nodes.length > 0 && !simTimeAdvanced && (
          <div className="connection-banner">
            Waiting for orbital propagation — satellites will begin moving shortly
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
            globeMode={globeMode}
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
          globeMode={globeMode}
          onToggleGlobeMode={() => setGlobeMode((m) => m === "blue-marble" ? "day-night" : "blue-marble")}
          onTopView={handleTopView}
          onFollowNode={handleFollowNode}
          onScreenshot={handleScreenshot}
        />
        {selection?.type !== "link" && selection != null && !cliDrawerOpen && (
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
        <button
          className="panel-toggle-tab"
          onClick={handlePanelToggle}
          title={panelOpen ? "Collapse panel" : "Expand panel"}
        >
          {panelOpen ? "\u203A" : "\u2039"}
        </button>
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
