// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Nodal Arc Visualization Frontend - main application component. */

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { Shell } from "./layout/Shell";
import { GlobeView } from "./globe/GlobeView";
import { Scene as R3FScene } from "./globe/r3f/Scene";
import { VisualizationErrorBoundary } from "./globe/VisualizationErrorBoundary";
import { TopologyView } from "./topology/TopologyView";
import { InfoPanel } from "./panels/InfoPanel";
import { FilterPanel } from "./panels/FilterPanel";
import { CliDrawer } from "./panels/CliDrawer";
import { LogPanel } from "./panels/LogPanel";
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
import { useAppState } from "./hooks/useAppState";
import { SessionWizard } from "./catalog/SessionWizard";
import { WS_URL, fetchApiKey } from "./config";
import { setLabelsEnabled, getLabelsEnabled } from "./globe/labels";
import { setGsLabelsEnabled, getGsLabelsEnabled } from "./globe/groundStations";
import type { TracedPath } from "./types";

import "./styles/variables.css";
import "./styles/reset.css";
import "./styles/layout.css";
import "./styles/panels.css";
import "./styles/toolbar.css";
import "./styles/topology.css";
import "./styles/time-controls.css";
import "./styles/catalog.css";
import "./styles/wizard.css";
import "./styles/explain.css";

// UX-2 cutover: the declarative R3F scene is now the default globe. `?legacy` opts back into
// the imperative globe as a one-flag fallback during the multi-body/fly-between capability build;
// the legacy globe (and this flag) are deleted once that work proves R3F in real use.
const USE_R3F =
  typeof window === "undefined" ||
  !new URLSearchParams(window.location.search).has("legacy");

export function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => { fetchApiKey().finally(() => setReady(true)); }, []);
  if (!ready) return null;
  return <AppInner />;
}

function AppInner() {
  const { snapshot, ephemeris, playbackState, connected, hasEverConnected, kicked, sessionTransitioning, sessionError, switchDetail, historicalMode, setHistoricalMode, fetchHistorical, sendMessage } =
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

  const { switching } = useSessionSwitcher(snapshot?.session_status ?? null);
  const playback = usePlayback(snapshot?.playback_paused, snapshot?.playback_speed);

  const appState = useAppState({
    snapshot,
    clearSelection,
    sessionTransitioning,
    switching,
  });

  const {
    showCatalog, hasEverDeployed, setHasEverDeployed, setShowCatalog,
    openCatalog, closeCatalog, activeSessionName, sessionStatus,
    viewMode, setViewMode, colorMode, setColorMode,
    showGroundLinks, setShowGroundLinks, showIslLinks, setShowIslLinks,
    showSatPaths, setShowSatPaths, showTrails, setShowTrails,
    globeMode, setGlobeMode, referenceFrame, toggleReferenceFrame, toggleView,
    cliDrawerOpen, setCliDrawerOpen, logPanelOpen, setLogPanelOpen,
    filterOpen, setFilterOpen,
    simTimeAdvanced, followNode, setFollowNode,
  } = appState;

  const [windowWidth, setWindowWidth] = useState(window.innerWidth);
  const [historicalPlaying, setHistoricalPlaying] = useState(false);
  const [visualizationError, setVisualizationError] = useState<string | null>(null);

  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const canSplit = windowWidth >= 1920;

  const [userTrace, setUserTrace] = useState<TracedPath | null>(null);
  const [visiblePlanes, setVisiblePlanes] = useState<Set<number> | null>(null);

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

  const handleFollowNode = useCallback(() => {
    if (!selection) return;
    setFollowNode((prev: boolean) => {
      const next = !prev;
      globeActionsRef.current?.setFollowTarget(next ? selection.id : null);
      return next;
    });
  }, [selection, setFollowNode]);

  const handleTopView = useCallback(() => { globeActionsRef.current?.flyToTopView(); }, []);
  const handleScreenshot = useCallback(() => { globeActionsRef.current?.captureScreenshot(); }, []);
  const handleFlyToNode = useCallback((nodeId: string) => { globeActionsRef.current?.flyToNode(nodeId); }, []);
  const handleVisualizationFatalError = useCallback((message: string) => {
    setVisualizationError(message);
  }, []);

  const keyboardActions = useMemo(
    () => ({
      onEscape: clearSelection,
      onCloseCatalog: showCatalog && hasEverDeployed ? closeCatalog : undefined,
      onToggleView: toggleView,
      onSetColorMode: setColorMode,
      onToggleGroundLinks: () => setShowGroundLinks((v: boolean) => !v),
      onToggleIslLinks: () => setShowIslLinks((v: boolean) => !v),
      onToggleSatPaths: () => setShowSatPaths((v: boolean) => !v),
      onToggleTrails: () => setShowTrails((v: boolean) => !v),
      onToggleHistorical: toggleHistorical,
      onPlayPause: () => {
        if (historicalMode) {
          setHistoricalPlaying((prev) => !prev);
        } else {
          playback.paused ? playback.resume() : playback.pause();
        }
      },
      onToggleGlobeMode: () => setGlobeMode((m: string) => m === "blue-marble" ? "day-night" : m === "day-night" ? "political" : "blue-marble"),
      onToggleReferenceFrame: toggleReferenceFrame,
      onFollowNode: handleFollowNode,
      onTopView: handleTopView,
      onToggleCli: () => setCliDrawerOpen((v: boolean) => !v),
      onTogglePanel: handlePanelToggle,
      onToggleFilter: () => setFilterOpen((v: boolean) => !v),
      onToggleLabels: () => setLabelsEnabled(!getLabelsEnabled()),
      onToggleGsLabels: () => setGsLabelsEnabled(!getGsLabelsEnabled()),
    }),
    [clearSelection, closeCatalog, showCatalog, hasEverDeployed, toggleView, toggleHistorical, handleFollowNode, handleTopView, historicalMode, playback, handlePanelToggle, toggleReferenceFrame, setColorMode, setShowGroundLinks, setShowIslLinks, setShowSatPaths, setShowTrails, setGlobeMode, setCliDrawerOpen, setFilterOpen],
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
      onOpenCatalog={openCatalog}
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
      {(switching || sessionTransitioning) && (
        <div className="session-switching-overlay">
          <div className="switching-box">
            <p>{sessionTransitioning ? "Switching session..." : "Deploying session..."}</p>
            <p style={{ fontSize: 10, color: "var(--text-dim)" }}>
              {switchDetail ?? snapshot?.session_status_detail ?? ""}
            </p>
          </div>
        </div>
      )}
      {sessionError && !switching && !sessionTransitioning && (
        <div className="session-switching-overlay">
          <div className="switching-box" style={{ borderColor: "var(--accent-red)" }}>
            <p style={{ color: "var(--accent-red)" }}>Session switch failed</p>
            <p style={{ fontSize: 10, color: "var(--text-dim)" }}>{sessionError}</p>
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
          Waiting for orbital propagation - satellites will begin moving shortly
        </div>
      )}
      {snapshot?.stale && (
        <div className="connection-banner" style={{ background: "rgba(200, 60, 60, 0.85)" }}>
          STALE DATA - waiting for upstream update
        </div>
      )}
      <div
        className={viewMode === "split" ? "split-pane" : "full-pane"}
        style={{ display: (viewMode === "topology" || viewMode === "dashboard") ? "none" : undefined }}
      >
        <VisualizationErrorBoundary onError={handleVisualizationFatalError}>
          {USE_R3F ? (
            <R3FScene
              snapshot={augmentedSnapshot}
              ephemeris={ephemeris}
              colorMode={colorMode}
              globeMode={globeMode}
              referenceFrame={referenceFrame}
              playbackPaused={playback.paused}
              playbackState={playbackState}
              showIslLinks={showIslLinks}
              showGroundLinks={showGroundLinks}
              showSatPaths={showSatPaths}
              showTrails={showTrails}
              selection={selection}
              onSelect={select}
              actionsRef={globeActionsRef}
            />
          ) : (
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
              onFatalError={handleVisualizationFatalError}
            />
          )}
        </VisualizationErrorBoundary>
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
        onToggleGroundLinks={() => setShowGroundLinks((v: boolean) => !v)}
        onToggleIslLinks={() => setShowIslLinks((v: boolean) => !v)}
        onToggleSatPaths={() => setShowSatPaths((v: boolean) => !v)}
        globeMode={globeMode}
        onToggleGlobeMode={() => setGlobeMode((m: string) => m === "blue-marble" ? "day-night" : "blue-marble")}
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
      {logPanelOpen && (
        <LogPanel
          events={snapshot?.ops_events ?? []}
          debugEvents={snapshot?.debug_events ?? []}
          debugSources={snapshot?.debug_sources ?? []}
          sendMessage={sendMessage}
          onClose={() => setLogPanelOpen(false)}
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
              onToggleIslLinks={() => setShowIslLinks((v: boolean) => !v)}
              onToggleGroundLinks={() => setShowGroundLinks((v: boolean) => !v)}
              onToggleSatPaths={() => setShowSatPaths((v: boolean) => !v)}
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
    <BottomBar snapshot={snapshot} connected={connected} historicalMode={historicalMode} logPanelOpen={logPanelOpen} onToggleLogPanel={() => setLogPanelOpen((v: boolean) => !v)} />
  );

  const toastsContent = <Toasts events={snapshot?.recent_events} />;

  const overlayContent = showCatalog ? (
    <SessionWizard
      onDeployStarted={() => { setShowCatalog(false); setHasEverDeployed(true); }}
      onClose={hasEverDeployed ? () => setShowCatalog(false) : undefined}
      deploying={switching}
      systemNotice={visualizationError ?? undefined}
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
