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
import { createEarth, createAtmosphere, createStarfield, createLights, updateSunPosition } from "./earth";
import { updateSatellites, animateSatellites, recolorAllSatellites, getSatellites } from "./satellites";
import { updateGroundStations, updateGSLabels, getGroundStations } from "./groundStations";
import { updateLinks, animateLinks } from "./links";
import { updateFlowPaths, animateFlowPaths } from "./flowPaths";
import { updateGroundTracks, clearGroundTracks } from "./groundTracks";
import { updateOrbitalTrails, flushTrails } from "./orbitalTrails";
import { setupRaycaster } from "./raycaster";
import { updateSelection, animateSelection } from "./selection";
import type { StateSnapshot, Selection, ColorMode } from "../types";

export interface GlobeActions {
  flyToTopView: () => void;
  setFollowTarget: (nodeId: string | null) => void;
  captureScreenshot: () => void;
  flyToNode: (nodeId: string) => void;
}

interface GlobeViewProps {
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  colorMode: ColorMode;
  showGroundTracks: boolean;
  showAllLinks: boolean;
  actionsRef?: MutableRefObject<GlobeActions | null>;
  followNode?: boolean;
}

export function GlobeView({
  snapshot,
  selection,
  onSelect,
  colorMode,
  showGroundTracks,
  showAllLinks,
  actionsRef,
  followNode: _followNode,
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
  const showGroundTracksRef = useRef(showGroundTracks);
  const showAllLinksRef = useRef(showAllLinks);
  const followTargetRef = useRef<string | null>(null);

  // Keep refs in sync
  snapshotRef.current = snapshot;
  colorModeRef.current = colorMode;
  selectionRef.current = selection;
  showAllLinksRef.current = showAllLinks;

  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  // Clear ground tracks when toggled off
  if (!showGroundTracks && showGroundTracksRef.current && sceneRef.current) {
    clearGroundTracks(sceneRef.current);
  }
  showGroundTracksRef.current = showGroundTracks;

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

    createStarfield(scene);
    createEarth(scene);
    createAtmosphere(scene);
    createLights(scene);

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
          const pos = sat?.mesh.position ?? gs?.sprite.position;
          if (pos) {
            // Move camera so the node faces the viewer, keeping orbit pivot at Earth center
            controls.target.set(0, 0, 0);
            const dist = camera.position.length();
            const dir = pos.clone().normalize();
            camera.position.copy(dir.multiplyScalar(dist));
            controls.update();
          }
        },
      };
    }

    // Animation loop
    let lastSnapshotRef: StateSnapshot | null = null;

    renderer.setAnimationLoop(() => {
      const dt = clockRef.current.getDelta();

      // Tab was backgrounded — flush trail history to avoid ghost lines
      if (dt > 1.0) {
        flushTrails();
      }

      const snap = snapshotRef.current;

      // Update entities when snapshot changes
      if (snap && snap !== lastSnapshotRef) {
        lastSnapshotRef = snap;
        updateSatellites(snap.nodes, scene, colorModeRef.current);
        updateGroundStations(snap.nodes, scene, labelContainer);
        updateLinks(snap.links, scene, showAllLinksRef.current);
        updateFlowPaths(snap.traced_paths, scene);
        updateSunPosition(snap.sim_time);
        if (showGroundTracksRef.current) {
          updateGroundTracks(snap.nodes, scene);
        }
      }

      // Follow selected node — rotate camera toward it, keep orbit pivot at origin
      if (followTargetRef.current) {
        const sat = getSatellites().get(followTargetRef.current);
        const gs = getGroundStations().get(followTargetRef.current);
        const targetPos = sat?.mesh.position ?? gs?.sprite.position;
        if (targetPos) {
          const dist = camera.position.length();
          const desiredDir = targetPos.clone().normalize();
          const currentDir = camera.position.clone().normalize();
          currentDir.lerp(desiredDir, 0.05);
          currentDir.normalize();
          camera.position.copy(currentDir.multiplyScalar(dist));
          controls.target.set(0, 0, 0);
        }
      }

      animateSatellites(dt);
      animateLinks();
      animateFlowPaths();
      updateOrbitalTrails(scene);
      updateSelection(selectionRef.current, scene, camera);
      animateSelection(camera);
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
      renderer.dispose();
      container.removeChild(renderer.domElement);
    };
  }, []);

  // Recolor satellites when color mode changes
  useEffect(() => {
    recolorAllSatellites(colorMode);
  }, [colorMode]);

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
