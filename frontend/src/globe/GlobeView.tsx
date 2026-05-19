// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** GlobeView — Three.js globe with satellites, ground stations, and links. */

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  CAMERA_FOV,
  CAMERA_DISTANCE,
  CAMERA_MIN_DISTANCE,
  CAMERA_MAX_DISTANCE,
  EARTH_RADIUS,
} from "../config";
import { createEarth, createAtmosphere, createStarfield, createLights, updateSunPosition, updateSunWorldDirection, setGlobeMode } from "./earth";
import { updateSatellites, animateSatellites, recolorAllSatellites, setEphemeris } from "./satellites";
import { getNodeWorldPosition, setEarthFrame } from "./positionLookup";
import { initWorkerBridge, sendEphemeris, requestFlush, destroyWorkerBridge } from "../sim/workerBridge";
import { resetSimClock, interpolatedSimTimeMs, setPlaybackPaused, onSnapshot } from "../sim/simClock";
import { gmstRadians, EARTH_ROTATION_RATE_RAD_S } from "./astronomy";
import { updateGroundStations, updateGSLabels } from "./groundStations";
import { updateLinks, animateLinks, clearLinks } from "./links";
import { updateFlowPaths, animateFlowPaths } from "./flowPaths";
import { updateOrbitalTrails, flushTrails, notifyEpochChange, clearTrails } from "./orbitalTrails";
import { updateOrbitPins, clearOrbitPins, reseedAllPins } from "./orbitPins";
import { updateAllOrbits, clearAllOrbits } from "./allOrbits";
import { setupGpuPicker } from "./gpuPicker";
import { updateLabels, animateLabels, clearLabels, setLabelContainer } from "./labels";
import { updateSelection, animateSelection } from "./selection";
import { updateCoverageFootprint } from "./coverageFootprint";
import { loadBoundaries, setBoundariesVisible } from "./boundaries";
import { VisualizationFailure } from "./VisualizationFailure";
import type { StateSnapshot, Selection, ColorMode, GlobeMode, ReferenceFrame } from "../types";

// Reusable temporaries for camera-math helpers (flyToNode, getNodeScreenPosition,
// follow-target), avoiding per-call allocation. World-space values only.
const _tmpWorldA = new THREE.Vector3();
const _tmpWorldB = new THREE.Vector3();
const _tmpNdc = new THREE.Vector3();
const _tmpDirA = new THREE.Vector3();
const _tmpDirB = new THREE.Vector3();

export interface GlobeActions {
  flyToTopView: () => void;
  setFollowTarget: (nodeId: string | null) => void;
  captureScreenshot: () => void;
  flyToNode: (nodeId: string) => void;
  getNodeScreenPosition: (nodeId: string) => { x: number; y: number; visible: boolean } | null;
}

interface GlobeViewProps {
  snapshot: StateSnapshot | null;
  ephemeris: import("../sim/ephemeris").SessionEphemeris | null;
  playbackState: import("../sim/ephemeris").PlaybackStateMsg | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  colorMode: ColorMode;
  globeMode: GlobeMode;
  showGroundLinks: boolean;
  showIslLinks: boolean;
  showSatPaths: boolean;
  referenceFrame: ReferenceFrame;
  playbackPaused: boolean;
  actionsRef?: MutableRefObject<GlobeActions | null>;
  onFatalError?: (message: string) => void;
}

function formatGlobeStartupError(error: unknown): string {
  const detail = error instanceof Error ? error.message : String(error);
  return `WebGL visualization could not start: ${detail}`;
}

export function GlobeView({
  snapshot,
  ephemeris,
  playbackState,
  selection,
  onSelect,
  colorMode,
  globeMode,
  showGroundLinks,
  showIslLinks,
  showSatPaths,
  referenceFrame,
  playbackPaused,
  actionsRef,
  onFatalError,
}: GlobeViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const labelContainerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const clockRef = useRef(new THREE.Clock());
  const snapshotRef = useRef<StateSnapshot | null>(null);
  const colorModeRef = useRef<ColorMode>(colorMode);
  const selectionRef = useRef<Selection | null>(null);
  const showGroundLinksRef = useRef(showGroundLinks);
  const showIslLinksRef = useRef(showIslLinks);
  const showSatPathsRef = useRef(showSatPaths);
  const referenceFrameRef = useRef<ReferenceFrame>(referenceFrame);
  const earthFrameRef = useRef<THREE.Group | null>(null);
  const starFrameRef = useRef<THREE.Group | null>(null);
  const followTargetRef = useRef<string | null>(null);
  const [fatalError, setFatalError] = useState<string | null>(null);

  // Keep refs in sync
  snapshotRef.current = snapshot;
  colorModeRef.current = colorMode;
  selectionRef.current = selection;
  showGroundLinksRef.current = showGroundLinks;
  showIslLinksRef.current = showIslLinks;
  showSatPathsRef.current = showSatPaths;
  referenceFrameRef.current = referenceFrame;

  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // Initialize Three.js scene
  useEffect(() => {
    const container = containerRef.current;
    const labelContainer = labelContainerRef.current;
    if (!container || !labelContainer) return;
    setLabelContainer(labelContainer);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d0d1a);
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(
      CAMERA_FOV,
      container.clientWidth / container.clientHeight,
      0.1,
      10000,
    );
    camera.position.set(0, CAMERA_DISTANCE * 0.5, CAMERA_DISTANCE * 0.87);
    cameraRef.current = camera;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    } catch (error) {
      const message = formatGlobeStartupError(error);
      setFatalError(message);
      onFatalError?.(message);
      sceneRef.current = null;
      cameraRef.current = null;
      rendererRef.current = null;
      if (actionsRef) actionsRef.current = null;
      return;
    }
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.minDistance = CAMERA_MIN_DISTANCE;
    controls.maxDistance = CAMERA_MAX_DISTANCE;
    controlsRef.current = controls;

    // Two reference-frame groups: earthFrame holds ECEF-referenced data
    // (Earth, atmosphere, sun, sats, GS, in-group geometry); starFrame
    // holds inertial data (starfield). Relative rotation is always +gmst;
    // which group carries the rotation depends on the view mode.
    const earthFrame = new THREE.Group();
    earthFrame.name = "earthFrame";
    scene.add(earthFrame);
    earthFrameRef.current = earthFrame;
    setEarthFrame(earthFrame);
    const starFrame = new THREE.Group();
    starFrame.name = "starFrame";
    scene.add(starFrame);
    starFrameRef.current = starFrame;

    createStarfield(starFrame);
    createEarth(earthFrame);
    createAtmosphere(earthFrame);
    createLights(scene, earthFrame);
    loadBoundaries(earthFrame);

    // Raycaster closes over getters that read current earthFrame rotation
    // and active frame angular velocity so Ctrl+click orbit-pin seeds use
    // the view frame that's active at click time.
    setupGpuPicker(
      renderer.domElement,
      camera,
      scene,
      (sel) => { onSelectRef.current(sel); },
      () => ({
        rotationRad: earthFrame.rotation.y,
        angularVelocityRadS:
          referenceFrameRef.current === "earth-inertial"
            ? EARTH_ROTATION_RATE_RAD_S
            : 0,
      }),
    );

    // Expose imperative actions
    if (actionsRef) {
      actionsRef.current = {
        flyToTopView: () => {
          // Fly camera to top-down view, altitude ~20000km equivalent
          const topDist = EARTH_RADIUS * 4;
          camera.position.set(0, topDist, 0);
          camera.lookAt(0, 0, 0);
          controls.target.set(0, 0, 0);
          controls.update();
        },
        setFollowTarget: (nodeId: string | null) => {
          followTargetRef.current = nodeId;
        },
        captureScreenshot: () => {
          // Draw timestamp watermark on the canvas
          const ctx2d = document.createElement("canvas");
          const w = renderer.domElement.width;
          const h = renderer.domElement.height;
          ctx2d.width = w;
          ctx2d.height = h;
          const ctx = ctx2d.getContext("2d")!;
          ctx.drawImage(renderer.domElement, 0, 0);
          const now = new Date().toISOString().replace("T", " ").substring(0, 19) + " UTC";
          const label = `Nodal Arc — ${now}`;
          ctx.font = `${Math.max(12, h * 0.015)}px monospace`;
          ctx.fillStyle = "rgba(255, 255, 255, 0.6)";
          ctx.textAlign = "right";
          ctx.fillText(label, w - 12, h - 12);

          const dataUrl = ctx2d.toDataURL("image/png");
          const link = document.createElement("a");
          link.download = `nodalarc-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
          link.href = dataUrl;
          link.click();
        },
        flyToNode: (nodeId: string) => {
          if (getNodeWorldPosition(nodeId, _tmpWorldA)) {
            controls.target.set(0, 0, 0);
            const dist = camera.position.length();
            _tmpWorldA.normalize();
            camera.position.copy(_tmpWorldA.multiplyScalar(dist));
            controls.update();
          }
        },
        getNodeScreenPosition: (nodeId: string) => {
          if (!getNodeWorldPosition(nodeId, _tmpWorldA)) return null;

          _tmpNdc.copy(_tmpWorldA).project(camera);

          // Behind camera or behind Earth
          if (_tmpNdc.z > 1) return { x: 0, y: 0, visible: false };

          // Earth occlusion check (same as updateGSLabels)
          const cameraPos = camera.position;
          _tmpDirA.copy(_tmpWorldA).sub(cameraPos).normalize();
          _tmpDirB.copy(cameraPos).multiplyScalar(-1).normalize();
          const dot = _tmpDirA.dot(_tmpDirB);
          const distToCenter = cameraPos.length();
          const sinAngle = EARTH_RADIUS / distToCenter;
          if (dot > Math.sqrt(1 - sinAngle * sinAngle) && _tmpWorldA.length() < distToCenter) {
            return { x: 0, y: 0, visible: false };
          }

          const w = container.clientWidth;
          const h = container.clientHeight;
          const x = (_tmpNdc.x * 0.5 + 0.5) * w;
          const y = (-_tmpNdc.y * 0.5 + 0.5) * h;
          return { x, y, visible: true };
        },
      };
    }

    // Animation loop
    let lastSnapshotRef: StateSnapshot | null = null;
    let lastConstellationName: string | null = null;

    renderer.setAnimationLoop(() => {
      const dt = clockRef.current.getDelta();

      // Tab was backgrounded — skip this frame for trails (don't flush history)
      const skipTrails = dt > 0.15;

      const snap = snapshotRef.current;

      // Flush trails and reset EMA when constellation changes (session switch)
      if (snap && snap.constellation_name !== lastConstellationName) {
        if (lastConstellationName !== null) {
          flushTrails();
          clearLinks();
          clearOrbitPins(scene);
          clearAllOrbits(scene);
          resetSimClock();
        }
        lastConstellationName = snap.constellation_name;
      }

      // Update entities when snapshot changes
      if (snap && snap !== lastSnapshotRef) {
        lastSnapshotRef = snap;
        // Feed sim clock with snapshot's sim_time for interpolation
        onSnapshot(snap.sim_time, performance.now());
        updateSatellites(snap.nodes, earthFrame, colorModeRef.current, snap.sim_time);
        updateGroundStations(snap.nodes, earthFrame, labelContainer);
        updateLinks(snap.links, earthFrame, showIslLinksRef.current);
        updateFlowPaths(snap.traced_paths, earthFrame);
        updateLabels(earthFrame);
        updateSunPosition(snap.sim_time);
      }

      // Apply view-frame rotation. The relative rotation between earthFrame
      // and starFrame is always +gmst(simTime); the active mode decides
      // which group carries it. Must run BEFORE any consumer that reads
      // getWorldPosition (selection ring, trails, orbits, GS labels).
      const interpMs = interpolatedSimTimeMs(performance.now());
      const gmstRad = interpMs !== null ? gmstRadians(interpMs / 1000) : 0;
      const mode = referenceFrameRef.current;
      if (mode === "earth-inertial") {
        earthFrame.rotation.y = gmstRad;
        starFrame.rotation.y = 0;
      } else {
        earthFrame.rotation.y = 0;
        starFrame.rotation.y = -gmstRad;
      }
      const angularVelocityRadS =
        mode === "earth-inertial" ? EARTH_ROTATION_RATE_RAD_S : 0;
      // DayNight shader's u_sunDirection is sampled from sun's world pos;
      // must run after earthFrame rotation is set.
      updateSunWorldDirection();

      // Follow selected node — rotate camera toward it, keep orbit pivot at origin.
      // World position required: camera rotation math operates in world space.
      if (followTargetRef.current) {
        if (getNodeWorldPosition(followTargetRef.current, _tmpWorldA)) {
          const dist = camera.position.length();
          _tmpWorldA.normalize();
          _tmpWorldB.copy(camera.position).normalize();
          _tmpWorldB.lerp(_tmpWorldA, 0.05).normalize();
          camera.position.copy(_tmpWorldB.multiplyScalar(dist));
          controls.target.set(0, 0, 0);
        }
      }

      animateSatellites(dt);
      animateLinks(showIslLinksRef.current, showGroundLinksRef.current);
      animateFlowPaths();
      if (!skipTrails) updateOrbitalTrails(scene);
      updateOrbitPins(scene);
      // Current view-frame parameters: rotation is whatever earthFrame
      // carries this frame; angular velocity is non-zero only in
      // earth-inertial view (dθ/dt = 0 when frame is static).
      updateAllOrbits(
        scene,
        showSatPathsRef.current,
        earthFrame.rotation.y,
        angularVelocityRadS,
      );
      updateSelection(selectionRef.current, scene, camera);
      animateSelection(camera);
      updateCoverageFootprint(selectionRef.current, earthFrame, camera);
      animateLabels(camera);
      controls.update();
      updateGSLabels(camera, labelContainer);
      renderer.render(scene, camera);
    });

    // Handle resize — use ResizeObserver so split/full layout changes are detected
    const resizeObs = new ResizeObserver(() => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (w === 0 || h === 0) return; // hidden via display:none
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    resizeObs.observe(container);

    return () => {
      resizeObs.disconnect();
      renderer.setAnimationLoop(null);
      clearOrbitPins(scene);
      clearAllOrbits(scene);
      clearLabels();
      clearLinks();
      clearTrails();
      destroyWorkerBridge();
      renderer.dispose();
      container.removeChild(renderer.domElement);
    };
  }, [actionsRef, onFatalError]);

  // Recolor satellites when color mode changes
  useEffect(() => {
    recolorAllSatellites(colorMode);
  }, [colorMode]);

  // Switch globe rendering mode
  useEffect(() => {
    setGlobeMode(globeMode);
    setBoundariesVisible(globeMode === "political" || globeMode === "day-night");
  }, [globeMode]);

  // Pass ephemeris to satellite renderer and Worker for propagation
  useEffect(() => {
    setEphemeris(ephemeris);
    if (ephemeris) {
      notifyEpochChange(ephemeris.epoch_id);
      initWorkerBridge();
      sendEphemeris(ephemeris);
      const simMs = new Date(ephemeris.sim_time).getTime();
      requestFlush(simMs / 1000, 1.0);
    }
  }, [ephemeris]);

  // Epoch suspension overlay (PRD v0.71 seek protocol)
  // On PlaybackState "seeking": show overlay, flush trails.
  // Cleared when new ephemeris arrives (seeking → playing transition).
  const [seeking, setSeeking] = useState(false);
  useEffect(() => {
    if (playbackState?.state === "seeking") {
      setSeeking(true);
      flushTrails();
    } else if (playbackState?.state === "playing") {
      setSeeking(false);
    }
  }, [playbackState]);

  // Freeze/unfreeze simClock on pause/resume (R-OME-008B: d(sim)/d(wall) = 0).
  // When paused, interpolatedSimTimeMs returns a constant, freezing both
  // satellite propagation and Earth rotation in lockstep.
  useEffect(() => {
    setPlaybackPaused(playbackPaused);
  }, [playbackPaused]);

  // On reference-frame toggle, reset frame-dependent world-space geometry:
  //   - Trail buffers: stored as world-space points; mixing points from
  //     two frames produces a meaningless trail. Clear and re-accumulate.
  //   - All-orbits rings: seeded from world pos+vel; invalid in the new
  //     frame. Clear; lazy-recreated from the render loop on next tick.
  //   - Orbit pins: re-seed rings using the new frame's parameters. Pin
  //     list (node IDs) preserved so user selections survive the toggle.
  // This useEffect fires AFTER the first render that has the new mode,
  // which means earthFrame.rotation.y already reflects the new frame.
  useEffect(() => {
    const scene = sceneRef.current;
    const earthFrame = earthFrameRef.current;
    if (!scene || !earthFrame) return;  // before mount completes
    flushTrails();
    clearAllOrbits(scene);
    const angularVelocityRadS =
      referenceFrame === "earth-inertial" ? EARTH_ROTATION_RATE_RAD_S : 0;
    reseedAllPins(earthFrame.rotation.y, angularVelocityRadS);
  }, [referenceFrame]);

  if (fatalError) {
    return <VisualizationFailure message={fatalError} />;
  }

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      <div
        ref={labelContainerRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          pointerEvents: "none",
          overflow: "hidden",
        }}
      />
      {seeking && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "rgba(0, 0, 0, 0.6)",
            pointerEvents: "none",
            zIndex: 10,
          }}
        >
          <div
            style={{
              color: "#fff",
              fontSize: "1.5rem",
              fontFamily: "monospace",
              padding: "1rem 2rem",
              border: "1px solid rgba(255, 255, 255, 0.3)",
              borderRadius: "8px",
              backgroundColor: "rgba(0, 0, 0, 0.7)",
            }}
          >
            Recalculating Epoch...
          </div>
        </div>
      )}
    </div>
  );
}
