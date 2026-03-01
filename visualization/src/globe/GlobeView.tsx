/** GlobeView — Three.js globe with satellites, ground stations, and links. */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  CAMERA_FOV,
  CAMERA_DISTANCE,
  CAMERA_MIN_DISTANCE,
  CAMERA_MAX_DISTANCE,
} from "../config";
import { createEarth, createAtmosphere, createLights } from "./earth";
import { updateSatellites, animateSatellites, recolorAllSatellites } from "./satellites";
import { updateGroundStations, updateGSLabels } from "./groundStations";
import { updateLinks, animateLinks } from "./links";
import { updateFlowPaths, animateFlowPaths } from "./flowPaths";
import { updateGroundTracks } from "./groundTracks";
import { setupRaycaster } from "./raycaster";
import type { StateSnapshot, Selection, ColorMode } from "../types";

interface GlobeViewProps {
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  colorMode: ColorMode;
  showGroundTracks: boolean;
  showAllLinks: boolean;
}

export function GlobeView({
  snapshot,
  selection,
  onSelect,
  colorMode,
  showGroundTracks,
  showAllLinks,
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

  // Keep refs in sync
  snapshotRef.current = snapshot;
  colorModeRef.current = colorMode;
  selectionRef.current = selection;
  showGroundTracksRef.current = showGroundTracks;
  showAllLinksRef.current = showAllLinks;

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

    const renderer = new THREE.WebGLRenderer({ antialias: true });
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

    createEarth(scene);
    createAtmosphere(scene);
    createLights(scene);

    // Raycaster for picking
    setupRaycaster(renderer.domElement, camera, scene, (sel) => {
      onSelectRef.current(sel);
    });

    // Animation loop
    let lastSnapshotRef: StateSnapshot | null = null;

    renderer.setAnimationLoop(() => {
      const dt = clockRef.current.getDelta();
      const snap = snapshotRef.current;

      // Update entities when snapshot changes
      if (snap && snap !== lastSnapshotRef) {
        lastSnapshotRef = snap;
        updateSatellites(snap.nodes, scene, colorModeRef.current);
        updateGroundStations(snap.nodes, scene, labelContainer);
        updateLinks(snap.links, scene, showAllLinksRef.current);
        updateFlowPaths(snap.traced_paths, scene);
        if (showGroundTracksRef.current) {
          updateGroundTracks(snap.nodes, scene);
        }
      }

      animateSatellites(dt);
      animateLinks();
      animateFlowPaths();
      controls.update();
      updateGSLabels(camera, labelContainer);
      renderer.render(scene, camera);
    });

    // Handle resize
    const onResize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
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
