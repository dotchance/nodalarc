// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Nodal Arc Visualization Frontend — main application component. */

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { Shell } from "./layout/Shell";
import { GlobeView } from "./globe/GlobeView";
import { TopologyView } from "./topology/TopologyView";
import { InfoPanel } from "./panels/InfoPanel";
import { FilterPanel } from "./panels/FilterPanel";
import { CliDrawer } from "./panels/CliDrawer";
import { NodePopover } from "./panels/NodePopover";
import { Toasts } from "./panels/Toasts";
import { Dashboard } from "./panels/Dashboard";
import { Toolbar } from "./toolbar/Toolbar";
import { TopBar } from "./bars/TopBar";
import { BottomBar } from "./bars/BottomBar";
import { TimeControls } from "./bars/TimeControls";
import { useSnapshot } from "./hooks/useSnapshot";
import { useSelection } from "./hooks/useSelection";
import { useKeyboard } from "./hooks/useKeyboard";
import { useSessionSwitcher } from "./hooks/useSessionSwitcher";
import { usePlayback } from "./hooks/usePlayback";
import { SessionWizard } from "./catalog/SessionWizard";
import { WS_URL, fetchApiKey } from "./config";
import { setLabelsEnabled, getLabelsEnabled } from "./globe/labels";
import { setGsLabelsEnabled, getGsLabelsEnabled } from "./globe/groundStations";
import type { ViewMode, ColorMode, GlobeMode, ReferenceFrame, TracedPath } from "./types";

const REFERENCE_FRAME_STORAGE_KEY = "nodalarc.referenceFrame";

import "./styles/variables.css";
import "./styles/reset.css";
import "./styles/layout.css";
import "./styles/panels.css";
import "./styles/toolbar.css";
import "./styles/topology.css";
import "./styles/time-controls.css";
import "./styles/catalog.css";
import "./styles/wizard.css";

export function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => { fetchApiKey().finally(() => setReady(true)); }, []);
  if (!ready) return null;
  return <AppInner />;
}

function AppInner() {
  const { snapshot, ephemeris, playbackState, connected, hasEverConnected, kicked, historicalMode, setHistoricalMode, fetchHistorical } =
    useSnapshot();
  const { selection, select, clearSelection } = useSelection();

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

  const [showCatalog, setShowCatalog] = useState(true);
  const [hasEverDeployed, setHasEverDeployed] = useState(false);

  const sessionStatus = snapshot?.session_status ?? "idle";
  const hasActiveSession = sessionStatus === "ready" || sessionStatus === "switching" || sessionStatus === "wiring";
  const activeSessionName = snapshot?.constellation_name ?? null;

  useEffect(() => {
    if (hasActiveSession && !hasEverDeployed) {
      setHasEverDeployed(true);
      setShowCatalog(false);
    }
  }, [hasActiveSession, hasEverDeployed]);

  const handleDeploy = useCallback((sessionId: string) => {
    if (hasActiveSession) {
      setShowCatalog(false);
      return;
    }
    switchSession(`configs/sessions/${sessionId}.yaml`);
    setShowCatalog(false);
    setHasEverDeployed(true);
  }, [switchSession, hasActiveSession]);

  const [viewMode, setViewMode] = useState<ViewMode>("globe");
  const [colorMode, setColorMode] = useState<ColorMode>("area");
  const [showGroundLinks, setShowGroundLinks] = useState(true);
  const [showIslLinks, setShowIslLinks] = useState(true);
  const [showSatPaths, setShowSatPaths] = useState(false);
  const [globeMode, setGlobeMode] = useState<GlobeMode>("blue-marble");
  const [referenceFrame, setReferenceFrame] = useState<ReferenceFrame>(() => {
    const saved = localStorage.getItem(REFERENCE_FRAME_STORAGE_KEY);
    return saved === "earth-fixed" ? "earth-fixed" : "earth-inertial";
  });
  useEffect(() => {
    localStorage.setItem(REFERENCE_FRAME_STORAGE_KEY, referenceFrame);
  }, [referenceFrame]);
  const toggleReferenceFrame = useCallback(() => {
    setReferenceFrame((f) => (f === "earth-fixed" ? "earth-inertial" : "earth-fixed"));
  }, []);
  const [followNode, setFollowNode] = useState(false);
  const [windowWidth, setWindowWidth] = useState(window.innerWidth);
  const [historicalPlaying, setHistoricalPlaying] = useState(false);

  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const canSplit = windowWidth >= 1920;

  const firstSimTimeRef = useRef<string | null>(null);
  const [simTimeAdvanced, setSimTimeAdvanced] = useState(false);

  useEffect(() => {
    if (switching || !snapshot || snapshot.nodes.length === 0) {
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
  const [userTrace, setUserTrace] = useState<TracedPath | null>(null);
  const [visiblePlanes, setVisiblePlanes] = useState<Set<number> | null>(null);
  const [filterOpen, setFilterOpen] = useState(false);

  const handleTogglePlane = useCallback((plane: number) => {
    setVisiblePlanes((prev) => {
      if (prev === null) {
        const allPlanes = new Set<number>();
        if (snapshot) {
          for (const n of snapshot.nodes) {
            if (n.node_type === "satellite" && n.plane != null) allPlanes.add(n.plane);
          }
        }
        allPlanes.delete(plane);
        return allPlanes;
      }
      const next = new Set(prev);
      if (next.has(plane)) next.delete(plane);
      else next.add(plane);
      return next;
    });
  }, [snapshot]);

  const handleShowAllPlanes = useCallback(() => setVisiblePlanes(null), []);
  const handleHideAllPlanes = useCallback(() => setVisiblePlanes(new Set()), []);

  const [panelOpen, setPanelOpen] = useState(true);
  const panelManualRef = useRef(false);
  const [panelWidth, setPanelWidth] = useState<number>(() => {
    const saved = localStorage.getItem("nodal_panel_width");
    return saved ? Math.max(280, Math.min(800, parseInt(saved, 10))) : 420;
  });

  useEffect(() => {
    if (selection && !panelManualRef.current) setPanelOpen(true);
    else if (!selection && !panelManualRef.current) setPanelOpen(false);
  }, [selection]);

  useEffect(() => {
    if (selection) panelManualRef.current = false;
  }, [selection]);

  const handlePanelToggle = useCallback(() => {
    setPanelOpen((v) => !v);
    panelManualRef.current = true;
  }, []);

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

  const handleTopView = useCallback(() => { globeActionsRef.current?.flyToTopView(); }, []);
  const handleScreenshot = useCallback(() => { globeActionsRef.current?.captureScreenshot(); }, []);
  const handleFlyToNode = useCallback((nodeId: string) => { globeActionsRef.current?.flyToNode(nodeId); }, []);

  const handleCloseCatalog = useCallback(() => {
    if (showCatalog && hasEverDeployed) setShowCatalog(false);
  }, [showCatalog, hasEverDeployed]);

  const keyboardActions = useMemo(
    () => ({
      onEscape: clearSelection,
      onCloseCatalog: showCatalog && hasEverDeployed ? handleCloseCatalog : undefined,
      onToggleView: toggleView,
      onSetColorMode: setColorMode,
      onToggleGroundLinks: () => setShowGroundLinks((v) => !v),
      onToggleIslLinks: () => setShowIslLinks((v) => !v),
      onToggleSatPaths: () => setShowSatPaths((v) => !v),
      onToggleHistorical: toggleHistorical,
      onPlayPause: () => {
        if (historicalMode) {
          setHistoricalPlaying((prev) => !prev);
        } else {
          playback.paused ? playback.resume() : playback.pause();
        }
      },
      onToggleGlobeMode: () => setGlobeMode((m) => m === "blue-marble" ? "day-night" : m === "day-night" ? "political" : "blue-marble"),
      onToggleReferenceFrame: toggleReferenceFrame,
      onFollowNode: handleFollowNode,
      onTopView: handleTopView,
      onToggleCli: () => setCliDrawerOpen((v) => !v),
      onTogglePanel: handlePanelToggle,
      onToggleFilter: () => setFilterOpen((v) => !v),
      onToggleLabels: () => setLabelsEnabled(!getLabelsEnabled()),
      onToggleGsLabels: () => setGsLabelsEnabled(!getGsLabelsEnabled()),
    }),
    [clearSelection, handleCloseCatalog, showCatalog, hasEverDeployed, toggleView, toggleHistorical, handleFollowNode, handleTopView, historicalMode, playback, handlePanelToggle, toggleReferenceFrame],
  );

  useKeyboard(keyboardActions);

  const prevViewModeRef = useRef(viewMode);
  useEffect(() => {
    const prev = prevViewModeRef.current;
    prevViewModeRef.current = viewMode;
    if (viewMode !== prev && (viewMode === "globe" || viewMode === "split") && selection) {
      globeActionsRef.current?.flyToNode(selection.id);
    }
  }, [viewMode, selection]);

  const augmentedSnapshot = useMemo(() => {
    if (!snapshot) return snapshot;
    const hasContinuous = snapshot.traced_paths.some(p => p.flow_id === "__continuous_trace__");
    if (hasContinuous) return snapshot;
    if (!userTrace) return snapshot;
    const serverPaths = snapshot.traced_paths.filter(p => p.flow_id !== "__user_trace__");
    return { ...snapshot, traced_paths: [...serverPaths, userTrace] };
  }, [snapshot, userTrace]);

  // --- Build zone content ---

  const topBarContent = (
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
      onSeekToNow={playback.seekToNow}
    />
  );

  const centerContent = (
    <>
      {kicked && (
        <div className="connection-startup-error">
          <div className="startup-error-box">
            <h2>Session taken over</h2>
            <p>Another browser connected and took control of this session.</p>
            <button onClick={() => window.location.reload()}>Reconnect</button>
          </div>
        </div>
      )}
      {!kicked && !connected && !hasEverConnected && (
        <div className="connection-startup-error">
          <div className="startup-error-box">
            <h2>Cannot connect to VS-API</h2>
            <p>Endpoint: {snapshot ? "" : WS_URL}</p>
            <button onClick={() => window.location.reload()}>Retry</button>
          </div>
        </div>
      )}
      {!kicked && !connected && hasEverConnected && (
        <div className="connection-banner">Connection lost. Reconnecting...</div>
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
      {!switching && sessionStatus === "wiring" && (
        <div className="session-switching-overlay">
          <div className="switching-box">
            <p>Data plane wiring in progress</p>
            <p style={{ fontSize: 10, color: "var(--text-dim)" }}>
              {snapshot?.session_status_detail ?? "Waiting for Node Agent..."}
            </p>
          </div>
        </div>
      )}
      {connected && !switching && sessionStatus !== "wiring" && (!snapshot || snapshot.nodes.length === 0) && (
        <div className="connection-banner">Initializing constellation...</div>
      )}
      {connected && !switching && snapshot && snapshot.nodes.length > 0 && !simTimeAdvanced && (
        <div className="connection-banner">
          Waiting for orbital propagation — satellites will begin moving shortly
        </div>
      )}
      {snapshot?.stale && (
        <div className="connection-banner" style={{ background: "rgba(200, 60, 60, 0.85)" }}>
          STALE DATA — waiting for upstream update
        </div>
      )}
      <div
        className={viewMode === "split" ? "split-pane" : "full-pane"}
        style={{ display: (viewMode === "topology" || viewMode === "dashboard") ? "none" : undefined }}
      >
        <GlobeView
          snapshot={augmentedSnapshot}
          ephemeris={ephemeris}
          playbackState={playbackState}
          selection={selection}
          onSelect={select}
          colorMode={colorMode}
          globeMode={globeMode}
          showGroundLinks={showGroundLinks}
          showIslLinks={showIslLinks}
          showSatPaths={showSatPaths}
          referenceFrame={referenceFrame}
          playbackPaused={playback.paused}
          actionsRef={globeActionsRef}
        />
      </div>
      <div
        className={viewMode === "split" ? "split-pane" : "full-pane"}
        style={{ display: (viewMode === "globe" || viewMode === "dashboard") ? "none" : undefined }}
      >
        <TopologyView
          snapshot={augmentedSnapshot}
          selection={selection}
          onSelect={select}
          onFlyTo={handleFlyToNode}
          colorMode={colorMode}
          showIslLinks={showIslLinks}
          showGroundLinks={showGroundLinks}
        />
      </div>
      {viewMode === "dashboard" && (
        <div className="full-pane" style={{ background: "var(--bg-main)", overflow: "auto" }}>
          <Dashboard snapshot={augmentedSnapshot} />
        </div>
      )}
      <Toolbar
        viewMode={viewMode}
        colorMode={colorMode}
        showGroundLinks={showGroundLinks}
        showIslLinks={showIslLinks}
        showSatPaths={showSatPaths}
        followNode={followNode}
        canSplit={canSplit}
        referenceFrame={referenceFrame}
        onViewMode={setViewMode}
        onColorMode={setColorMode}
        onToggleGroundLinks={() => setShowGroundLinks((v) => !v)}
        onToggleIslLinks={() => setShowIslLinks((v) => !v)}
        onToggleSatPaths={() => setShowSatPaths((v) => !v)}
        globeMode={globeMode}
        onToggleGlobeMode={() => setGlobeMode((m) => m === "blue-marble" ? "day-night" : "blue-marble")}
        onToggleReferenceFrame={toggleReferenceFrame}
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
      {filterOpen && (
        <div className="filter-panel-overlay" onClick={() => setFilterOpen(false)}>
          <div className="filter-panel-drawer" onClick={(e) => e.stopPropagation()}>
            <FilterPanel
              snapshot={augmentedSnapshot}
              showIslLinks={showIslLinks}
              showGroundLinks={showGroundLinks}
              showSatPaths={showSatPaths}
              colorMode={colorMode}
              onToggleIslLinks={() => setShowIslLinks((v) => !v)}
              onToggleGroundLinks={() => setShowGroundLinks((v) => !v)}
              onToggleSatPaths={() => setShowSatPaths((v) => !v)}
              onSetColorMode={setColorMode}
              visiblePlanes={visiblePlanes}
              onTogglePlane={handleTogglePlane}
              onShowAllPlanes={handleShowAllPlanes}
              onHideAllPlanes={handleHideAllPlanes}
            />
          </div>
        </div>
      )}
    </>
  );

  const rightPanelContent = (
    <InfoPanel
      snapshot={augmentedSnapshot}
      selection={selection}
      onSelect={select}
      onFlyTo={handleFlyToNode}
      onTraceResult={setUserTrace}
    />
  );

  const bottomBarContent = (
    <BottomBar snapshot={snapshot} connected={connected} historicalMode={historicalMode} />
  );

  const toastsContent = <Toasts events={snapshot?.recent_events} />;

  const overlayContent = showCatalog ? (
    <SessionWizard
      onDeployStarted={() => { setShowCatalog(false); setHasEverDeployed(true); }}
      onClose={hasEverDeployed ? () => setShowCatalog(false) : undefined}
      deploying={switching}
      fallbackSessions={sessions}
      onFallbackDeploy={handleDeploy}
    />
  ) : undefined;

  const historicalControlsContent = historicalMode ? (
    <TimeControls
      onSeek={fetchHistorical}
      startTime={snapshot?.sim_time ?? new Date().toISOString()}
      endTime={new Date().toISOString()}
      events={snapshot?.recent_events}
      externalPlaying={historicalPlaying}
      onPlayingChange={setHistoricalPlaying}
    />
  ) : undefined;

  return (
    <Shell
      topBar={topBarContent}
      center={centerContent}
      rightPanel={rightPanelContent}
      bottomBar={bottomBarContent}
      overlay={overlayContent}
      toasts={toastsContent}
      historicalControls={historicalControlsContent}
      historicalMode={historicalMode}
      panelOpen={panelOpen}
      onPanelToggle={handlePanelToggle}
      panelWidth={panelWidth}
      onPanelWidthChange={setPanelWidth}
    />
  );
}
