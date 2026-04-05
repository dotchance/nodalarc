// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** GlobeView — Three.js globe with satellites, ground stations, and links. */

import { useEffect, useRef, type MutableRefObject } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  CAMERA_FOV,
  CAMERA_DISTANCE,
  CAMERA_MIN_DISTANCE,
  CAMERA_MAX_DISTANCE,
  EARTH_RADIUS,
} from "../config";
import { createEarth, createAtmosphere, createStarfield, createLights, updateSunPosition, setGlobeMode } from "./earth";
import { updateSatellites, animateSatellites, recolorAllSatellites, getSatellites } from "./satellites";
import { resetSimClock } from "../sim/simClock";
import { updateGroundStations, updateGSLabels, getGroundStations } from "./groundStations";
import { updateLinks, animateLinks } from "./links";
import { updateFlowPaths, animateFlowPaths } from "./flowPaths";
import { updateOrbitalTrails, flushTrails } from "./orbitalTrails";
import { updateOrbitPins, clearOrbitPins } from "./orbitPins";
import { updateAllOrbits, clearAllOrbits } from "./allOrbits";
import { setupRaycaster } from "./raycaster";
import { updateSelection, animateSelection } from "./selection";
import { updateCoverageFootprint } from "./coverageFootprint";
import type { StateSnapshot, Selection, ColorMode, GlobeMode } from "../types";

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
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  colorMode: ColorMode;
  globeMode: GlobeMode;
  showGroundLinks: boolean;
  showIslLinks: boolean;
  showSatPaths: boolean;
  actionsRef?: MutableRefObject<GlobeActions | null>;
}

export function GlobeView({
  snapshot,
  selection,
  onSelect,
  colorMode,
  globeMode,
  showGroundLinks,
  showIslLinks,
  showSatPaths,
  actionsRef,
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
  const followTargetRef = useRef<string | null>(null);

  // Keep refs in sync
  snapshotRef.current = snapshot;
  colorModeRef.current = colorMode;
  selectionRef.current = selection;
  showGroundLinksRef.current = showGroundLinks;
  showIslLinksRef.current = showIslLinks;
  showSatPathsRef.current = showSatPaths;

  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // Initialize Three.js scene
  useEffect(() => {
    const container = containerRef.current;
    const labelContainer = labelContainerRef.current;
    if (!container || !labelContainer) return;

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

    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
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
    // holds inertial data (starfield). Rotations are wired up in Phase 6;
    // both remain at identity here so visual output is unchanged.
    const earthFrame = new THREE.Group();
    earthFrame.name = "earthFrame";
    scene.add(earthFrame);
    const starFrame = new THREE.Group();
    starFrame.name = "starFrame";
    scene.add(starFrame);

    createStarfield(starFrame);
    createEarth(earthFrame);
    createAtmosphere(earthFrame);
    createLights(scene, earthFrame);

    // Raycaster for picking
    setupRaycaster(renderer.domElement, camera, scene, (sel) => {
      onSelectRef.current(sel);
    });

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
          const sat = getSatellites().get(nodeId);
          const gs = getGroundStations().get(nodeId);
          // World position required: camera direction math operates in
          // world space. Sats/GS live in earthFrame; local coords would
          // misdirect the camera under any non-identity group rotation.
          const target = sat?.mesh.getWorldPosition(_tmpWorldA)
            ?? gs?.sprite.getWorldPosition(_tmpWorldA);
          if (target) {
            controls.target.set(0, 0, 0);
            const dist = camera.position.length();
            _tmpWorldA.normalize();
            camera.position.copy(_tmpWorldA.multiplyScalar(dist));
            controls.update();
          }
        },
        getNodeScreenPosition: (nodeId: string) => {
          const sat = getSatellites().get(nodeId);
          const gs = getGroundStations().get(nodeId);
          const worldPos = sat
            ? sat.mesh.getWorldPosition(_tmpWorldA)
            : gs
              ? gs.sprite.getWorldPosition(_tmpWorldA)
              : null;
          if (!worldPos) return null;

          _tmpNdc.copy(worldPos).project(camera);

          // Behind camera or behind Earth
          if (_tmpNdc.z > 1) return { x: 0, y: 0, visible: false };

          // Earth occlusion check (same as updateGSLabels)
          const cameraPos = camera.position;
          _tmpDirA.copy(worldPos).sub(cameraPos).normalize();
          _tmpDirB.copy(cameraPos).multiplyScalar(-1).normalize();
          const dot = _tmpDirA.dot(_tmpDirB);
          const distToCenter = cameraPos.length();
          const sinAngle = EARTH_RADIUS / distToCenter;
          if (dot > Math.sqrt(1 - sinAngle * sinAngle) && worldPos.length() < distToCenter) {
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
          clearOrbitPins(scene);
          clearAllOrbits(scene);
          resetSimClock();
        }
        lastConstellationName = snap.constellation_name;
      }

      // Update entities when snapshot changes
      if (snap && snap !== lastSnapshotRef) {
        lastSnapshotRef = snap;
        updateSatellites(snap.nodes, earthFrame, colorModeRef.current, snap.sim_time);
        updateGroundStations(snap.nodes, earthFrame, labelContainer);
        updateLinks(snap.links, scene, showIslLinksRef.current);
        updateFlowPaths(snap.traced_paths, scene);
        updateSunPosition(snap.sim_time);
      }

      // Follow selected node — rotate camera toward it, keep orbit pivot at origin.
      // World position required: camera rotation math operates in world space.
      if (followTargetRef.current) {
        const sat = getSatellites().get(followTargetRef.current);
        const gs = getGroundStations().get(followTargetRef.current);
        const targetPos = sat
          ? sat.mesh.getWorldPosition(_tmpWorldA)
          : gs
            ? gs.sprite.getWorldPosition(_tmpWorldA)
            : null;
        if (targetPos) {
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
      updateAllOrbits(scene, showSatPathsRef.current);
      updateSelection(selectionRef.current, scene, camera);
      animateSelection(camera);
      updateCoverageFootprint(selectionRef.current, scene, camera);
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
      renderer.dispose();
      container.removeChild(renderer.domElement);
    };
  }, []);

  // Recolor satellites when color mode changes
  useEffect(() => {
    recolorAllSatellites(colorMode);
  }, [colorMode]);

  // Switch globe rendering mode
  useEffect(() => {
    setGlobeMode(globeMode);
  }, [globeMode]);

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
    </div>
  );
}
