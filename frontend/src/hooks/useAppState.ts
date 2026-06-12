// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Application UI state machine extracted from App.tsx for testability.
 *
 *  Owns view/display toggles, catalog visibility, CLI drawer state,
 *  and session transition side effects. Takes external inputs (snapshot,
 *  selection, sessionTransitioning) and returns computed state + actions.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import type {
  ViewMode,
  ColorMode,
  GlobeMode,
  ReferenceFrame,
  StateSnapshot,
} from "../types";

const REFERENCE_FRAME_STORAGE_KEY = "nodalarc.referenceFrame";

interface AppStateInputs {
  snapshot: StateSnapshot | null;
  clearSelection: () => void;
  sessionTransitioning: boolean;
  switching: boolean;
}

export function useAppState(inputs: AppStateInputs) {
  const { snapshot, clearSelection, sessionTransitioning, switching } = inputs;

  // --- Catalog / wizard ---
  const [showCatalog, setShowCatalog] = useState(true);
  const [hasEverDeployed, setHasEverDeployed] = useState(false);

  const sessionStatus = snapshot?.session_status ?? "idle";
  const hasActiveSession =
    sessionStatus === "ready" ||
    sessionStatus === "switching" ||
    sessionStatus === "wiring";
  const activeSessionName = snapshot?.constellation_name ?? null;

  useEffect(() => {
    if (hasActiveSession && !hasEverDeployed) {
      setHasEverDeployed(true);
      setShowCatalog(false);
    }
  }, [hasActiveSession, hasEverDeployed]);

  const openCatalog = useCallback(() => setShowCatalog(true), []);
  const closeCatalog = useCallback(() => {
    if (showCatalog && hasEverDeployed) setShowCatalog(false);
  }, [showCatalog, hasEverDeployed]);

  // --- View / display toggles ---
  const [viewMode, setViewMode] = useState<ViewMode>("globe");
  const [colorMode, setColorMode] = useState<ColorMode>("area");
  const [showGroundLinks, setShowGroundLinks] = useState(true);
  const [showIslLinks, setShowIslLinks] = useState(true);
  const [showSatPaths, setShowSatPaths] = useState(false);
  const [showGroundTracks, setShowGroundTracks] = useState(false);
  const [showTrails, setShowTrails] = useState(true);
  const [globeMode, setGlobeMode] = useState<GlobeMode>("blue-marble");
  const [referenceFrame, setReferenceFrame] = useState<ReferenceFrame>(() => {
    const saved = localStorage.getItem(REFERENCE_FRAME_STORAGE_KEY);
    return saved === "earth-fixed" ? "earth-fixed" : "earth-inertial";
  });

  useEffect(() => {
    localStorage.setItem(REFERENCE_FRAME_STORAGE_KEY, referenceFrame);
  }, [referenceFrame]);

  const toggleReferenceFrame = useCallback(() => {
    setReferenceFrame((f) =>
      f === "earth-fixed" ? "earth-inertial" : "earth-fixed",
    );
  }, []);

  const toggleView = useCallback(() => {
    setViewMode((prev) => (prev === "globe" ? "topology" : "globe"));
  }, []);

  // --- CLI drawer ---
  const [cliDrawerOpen, setCliDrawerOpen] = useState(false);
  const [logPanelOpen, setLogPanelOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);

  useEffect(() => {
    if (sessionTransitioning) {
      setCliDrawerOpen(false);
      clearSelection();
    }
  }, [sessionTransitioning, clearSelection]);

  // --- Sim time tracking ---
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

  // --- Follow node ---
  const [followNode, setFollowNode] = useState(false);

  return {
    // Catalog
    showCatalog,
    hasEverDeployed,
    setHasEverDeployed,
    setShowCatalog,
    openCatalog,
    closeCatalog,
    hasActiveSession,
    activeSessionName,
    sessionStatus,

    // View / display
    viewMode,
    setViewMode,
    colorMode,
    setColorMode,
    showGroundLinks,
    setShowGroundLinks,
    showIslLinks,
    setShowIslLinks,
    showSatPaths,
    showGroundTracks,
    setShowSatPaths,
    setShowGroundTracks,
    showTrails,
    setShowTrails,
    globeMode,
    setGlobeMode,
    referenceFrame,
    toggleReferenceFrame,
    toggleView,

    // Panels
    cliDrawerOpen,
    setCliDrawerOpen,
    logPanelOpen,
    setLogPanelOpen,
    filterOpen,
    setFilterOpen,

    // Misc
    simTimeAdvanced,
    followNode,
    setFollowNode,
  };
}
