// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Nodal Arc Visualization Frontend - main application component. */

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { Shell } from "./layout/Shell";
import { Scene as R3FScene } from "./globe/r3f/Scene";
import { VisualizationErrorBoundary } from "./globe/VisualizationErrorBoundary";
import { TopologyView } from "./topology/TopologyView";
import { InfoPanel } from "./panels/InfoPanel";
import { FilterPanel } from "./panels/FilterPanel";
import { CliDrawer } from "./panels/CliDrawer";
import { LogPanel } from "./panels/LogPanel";
import { NodePopover } from "./panels/NodePopover";
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
import { filterSnapshotForRender, nodeSegmentId } from "./filters/renderSnapshot";
import { selectionTypeForNode } from "./networkIdentity";
import { SessionWizard } from "./catalog/SessionWizard";
import { ShortcutHelp } from "./panels/ShortcutHelp";
import { WS_URL, fetchApiKey } from "./config";
import { setLabelsEnabled, getLabelsEnabled } from "./globe/labels";
import { setGsLabelsEnabled, getGsLabelsEnabled } from "./globe/groundStations";
import type { TracedPath } from "./types";
import type { GlobeActions } from "./globe/actions";

import "./styles/fonts.css";
import "./styles/variables.css";
import "./styles/reset.css";
import "./ui/ui.css";
import "./styles/layout.css";
import "./styles/topbar.css";
import "./styles/bottombar.css";
import "./styles/log-window.css";
import "./styles/cli-drawer.css";
import "./styles/popover.css";
import "./styles/panels.css";
import "./styles/toolbar.css";
import "./styles/topology.css";
import "./styles/time-controls.css";
import "./styles/catalog.css";
import "./styles/wizard.css";
import "./styles/explain.css";

const HISTORICAL_WINDOW_MS = 60 * 60 * 1000;

function subtractMsIso(iso: string, deltaMs: number): string {
  const t = new Date(iso).getTime();
  const base = Number.isFinite(t) ? t : Date.now();
  return new Date(base - deltaMs).toISOString();
}

export function App() {
  const [ready, setReady] = useState(false);
  useEffect(() => { fetchApiKey().finally(() => setReady(true)); }, []);
  if (!ready) return null;
  return <AppInner />;
}

function AppInner() {
  const { snapshot, ephemeris, playbackState, connected, hasEverConnected, kicked, sessionTransitioning, sessionError, switchDetail, historicalMode, setHistoricalMode, fetchHistorical, historicalError, sendMessage } =
    useSnapshot();
  const { selection, select, clearSelection, anchorGsId } = useSelection();
  const preselectedAppliedRef = useRef(false);

  useEffect(() => {
    if (preselectedAppliedRef.current || !snapshot) return;
    const params = new URLSearchParams(window.location.search);
    const preselected = params.get("selected");
    if (!preselected) {
      preselectedAppliedRef.current = true;
      return;
    }
    const node = snapshot.nodes.find((candidate) => candidate.node_id === preselected);
    if (!node) return;
    select({ type: selectionTypeForNode(node), id: preselected });
    preselectedAppliedRef.current = true;
  }, [snapshot, select]);

  const { sessions, switching, switchSession } = useSessionSwitcher(sessionTransitioning);
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
  const [showHelp, setShowHelp] = useState(false);
  const [historicalPlaying, setHistoricalPlaying] = useState(false);
  const [historicalRangeEnd, setHistoricalRangeEnd] = useState<string | null>(null);
  const [visualizationError, setVisualizationError] = useState<string | null>(null);

  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const canSplit = windowWidth >= 1280;

  const [userTrace, setUserTrace] = useState<TracedPath | null>(null);
  const [visiblePlanes, setVisiblePlanes] = useState<Set<number> | null>(null);
  const [visibleSegments, setVisibleSegments] = useState<Set<string> | null>(null);
  const globeActionsRef = useRef<GlobeActions | null>(null);

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

  const handleToggleSegment = useCallback((segmentId: string) => {
    setVisibleSegments((prev) => {
      if (prev === null) {
        const allSegments = new Set<string>();
        if (snapshot) {
          for (const node of snapshot.nodes) allSegments.add(nodeSegmentId(node));
        }
        allSegments.delete(segmentId);
        return allSegments;
      }
      const next = new Set(prev);
      if (next.has(segmentId)) next.delete(segmentId);
      else next.add(segmentId);
      return next;
    });
  }, [snapshot]);

  const handleShowAllSegments = useCallback(() => setVisibleSegments(null), []);
  const handleHideAllSegments = useCallback(() => setVisibleSegments(new Set()), []);
  const handleFlyToSegment = useCallback((segmentId: string) => {
    const nodeIds = (snapshot?.nodes ?? [])
      .filter((node) => nodeSegmentId(node) === segmentId)
      .map((node) => node.node_id);
    globeActionsRef.current?.flyToSegment(nodeIds);
  }, [snapshot]);

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

  const toggleHistorical = useCallback(() => {
    const next = !historicalMode;
    setHistoricalPlaying(false);
    if (next) {
      setHistoricalRangeEnd(snapshot?.sim_time ?? new Date().toISOString());
    }
    setHistoricalMode(next);
  }, [historicalMode, setHistoricalMode, snapshot?.sim_time]);

  const focusCurrentSelection = useCallback((follow = false): boolean => {
    if (!selection) {
      if (!follow) globeActionsRef.current?.frameScene();
      return !follow;
    }
    if (selection.type === "link") {
      const link = snapshot?.links.find(
        (candidate) => `${[candidate.node_a, candidate.node_b].sort().join(":")}` === selection.id,
      );
      if (!link) return false;
      globeActionsRef.current?.focusLink(link.node_a, link.node_b, { follow });
      return true;
    }
    globeActionsRef.current?.focusNode(selection.id, { follow });
    return true;
  }, [selection, snapshot?.links]);

  const handleFollowNode = useCallback(() => {
    setFollowNode((prev: boolean) => {
      if (prev) {
        globeActionsRef.current?.setFollowTarget(null);
        return false;
      }
      return focusCurrentSelection(true);
    });
  }, [focusCurrentSelection, setFollowNode]);

  const handleTopView = useCallback(() => { globeActionsRef.current?.flyToTopView(); }, []);
  const handleFrameScene = useCallback(() => { globeActionsRef.current?.frameScene(); }, []);
  const handleFrameSelection = useCallback(() => {
    focusCurrentSelection(false);
  }, [focusCurrentSelection]);
  const handleScreenshot = useCallback(() => { globeActionsRef.current?.captureScreenshot(); }, []);
  const handleFlyToNode = useCallback((nodeId: string) => { globeActionsRef.current?.focusNode(nodeId); }, []);
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
      onFrameSelection: handleFrameSelection,
      onFrameScene: handleFrameScene,
      onTopView: handleTopView,
      onToggleCli: () => setCliDrawerOpen((v: boolean) => !v),
      onTogglePanel: handlePanelToggle,
      onToggleFilter: () => setFilterOpen((v: boolean) => !v),
      onToggleLabels: () => setLabelsEnabled(!getLabelsEnabled()),
      onToggleGsLabels: () => setGsLabelsEnabled(!getGsLabelsEnabled()),
      onShowHelp: () => setShowHelp(true),
    }),
    [clearSelection, closeCatalog, showCatalog, hasEverDeployed, toggleView, toggleHistorical, handleFollowNode, handleFrameSelection, handleFrameScene, handleTopView, historicalMode, playback, handlePanelToggle, toggleReferenceFrame, setColorMode, setShowGroundLinks, setShowIslLinks, setShowSatPaths, setShowTrails, setGlobeMode, setCliDrawerOpen, setFilterOpen],
  );

  useKeyboard(keyboardActions);

  const prevViewModeRef = useRef(viewMode);
  useEffect(() => {
    const prev = prevViewModeRef.current;
    prevViewModeRef.current = viewMode;
    if (viewMode !== prev && (viewMode === "globe" || viewMode === "split") && selection) {
      focusCurrentSelection(false);
    }
  }, [viewMode, selection, focusCurrentSelection]);

  const augmentedSnapshot = useMemo(() => {
    if (!snapshot) return snapshot;
    const hasContinuous = snapshot.traced_paths.some(p => p.flow_id === "__continuous_trace__");
    if (hasContinuous) return snapshot;
    if (!userTrace) return snapshot;
    const serverPaths = snapshot.traced_paths.filter(p => p.flow_id !== "__user_trace__");
    return { ...snapshot, traced_paths: [...serverPaths, userTrace] };
  }, [snapshot, userTrace]);

  const renderedSnapshot = useMemo(
    () => filterSnapshotForRender(augmentedSnapshot, visibleSegments, visiblePlanes),
    [augmentedSnapshot, visibleSegments, visiblePlanes],
  );

  // --- Build zone content ---

  const topBarContent = (
    <TopBar
      snapshot={snapshot}
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
      onShowHelp={() => setShowHelp(true)}
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
      <div className="banner-stack">
        {!kicked && !connected && hasEverConnected && (
          <div className="connection-banner">Connection lost. Reconnecting...</div>
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
          <div className="connection-banner connection-banner--fail">
            STALE DATA - waiting for upstream update
          </div>
        )}
      </div>
      {showHelp && <ShortcutHelp onClose={() => setShowHelp(false)} />}
      {(switching || sessionTransitioning) && (
        <div className="session-switching-overlay">
          <div className="switching-box">
            <p>{sessionTransitioning ? "Switching session..." : "Deploying session..."}</p>
            <p className="switching-detail">{switchDetail ?? snapshot?.session_status_detail ?? ""}</p>
          </div>
        </div>
      )}
      {sessionError && !switching && !sessionTransitioning && (
        <div className="session-switching-overlay">
          <div className="switching-box switching-box--fail">
            <p>Session switch failed</p>
            <p className="switching-detail">{sessionError}</p>
          </div>
        </div>
      )}
      {!switching && sessionStatus === "wiring" && (
        <div className="session-switching-overlay">
          <div className="switching-box">
            <p>Data plane wiring in progress</p>
            <p className="switching-detail">{snapshot?.session_status_detail ?? "Waiting for Node Agent..."}</p>
          </div>
        </div>
      )}
      <div
        className={viewMode === "split" ? "split-pane" : "full-pane"}
        style={{ display: (viewMode === "topology" || viewMode === "dashboard") ? "none" : undefined }}
      >
        <VisualizationErrorBoundary onError={handleVisualizationFatalError}>
          <R3FScene
            snapshot={renderedSnapshot}
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
        </VisualizationErrorBoundary>
      </div>
      <div
        className={viewMode === "split" ? "split-pane" : "full-pane"}
        style={{ display: (viewMode === "globe" || viewMode === "dashboard") ? "none" : undefined }}
      >
        <TopologyView
          snapshot={renderedSnapshot}
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
        filterOpen={filterOpen}
        canSplit={canSplit}
        referenceFrame={referenceFrame}
        onViewMode={setViewMode}
        onColorMode={setColorMode}
        onToggleGroundLinks={() => setShowGroundLinks((v: boolean) => !v)}
        onToggleIslLinks={() => setShowIslLinks((v: boolean) => !v)}
        onToggleSatPaths={() => setShowSatPaths((v: boolean) => !v)}
        onToggleFilter={() => setFilterOpen((v: boolean) => !v)}
        globeMode={globeMode}
        onGlobeMode={setGlobeMode}
        onToggleReferenceFrame={toggleReferenceFrame}
        onTopView={handleTopView}
        onFrameScene={handleFrameScene}
        onFollowNode={handleFollowNode}
        onScreenshot={handleScreenshot}
      />
      {selection?.type !== "link" && selection != null && !cliDrawerOpen && (
        <NodePopover
          snapshot={snapshot}
          selection={selection}
          onClose={clearSelection}
          onOpenCli={() => setCliDrawerOpen(true)}
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
          recentEvents={snapshot?.recent_events ?? []}
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
              visibleSegments={visibleSegments}
              onToggleSegment={handleToggleSegment}
              onShowAllSegments={handleShowAllSegments}
              onHideAllSegments={handleHideAllSegments}
              onFlyToSegment={handleFlyToSegment}
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
      anchorGsId={anchorGsId}
      onSelect={select}
      onTraceResult={setUserTrace}
    />
  );

  const bottomBarContent = (
    <BottomBar snapshot={snapshot} connected={connected} historicalMode={historicalMode} logPanelOpen={logPanelOpen} onToggleLogPanel={() => setLogPanelOpen((v: boolean) => !v)} />
  );

  const overlayContent = showCatalog ? (
    <SessionWizard
      onDeployStarted={() => { setShowCatalog(false); setHasEverDeployed(true); }}
      onClose={hasEverDeployed ? () => setShowCatalog(false) : undefined}
      deploying={switching}
      systemNotice={visualizationError ?? undefined}
      sessions={sessions}
      onLaunchSession={switchSession}
    />
  ) : undefined;

  const historicalEndTime = historicalRangeEnd ?? snapshot?.sim_time ?? new Date().toISOString();
  const historicalStartTime = subtractMsIso(historicalEndTime, HISTORICAL_WINDOW_MS);
  const historicalControlsContent = historicalMode ? (
    <TimeControls
      onSeek={fetchHistorical}
      startTime={historicalStartTime}
      endTime={historicalEndTime}
      events={snapshot?.recent_events}
      externalPlaying={historicalPlaying}
      onPlayingChange={setHistoricalPlaying}
      statusMessage={historicalError}
    />
  ) : undefined;

  return (
    <Shell
      topBar={topBarContent}
      center={centerContent}
      rightPanel={rightPanelContent}
      bottomBar={bottomBarContent}
      overlay={overlayContent}
      historicalControls={historicalControlsContent}
      historicalMode={historicalMode}
      centerSplit={viewMode === "split"}
      panelOpen={panelOpen}
      onPanelToggle={handlePanelToggle}
      panelWidth={panelWidth}
      onPanelWidthChange={setPanelWidth}
    />
  );
}
