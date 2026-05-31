// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The R3F scene root and orchestrator. Composes the full declarative scene — Earth +
 * starfield, instanced constellation, ground stations, links/beams, flow paths, coverage
 * footprint, orbital trails, full-constellation orbit rings, ground tracks, the selection
 * overlay, and the HTML label layer — and drives the reference-frame rotation (FrameDriver).
 * It owns the cross-cutting lifecycle the legacy GlobeView held: feeding the EMA sim-clock
 * per snapshot, pausing the clock, driving the SGP4 worker on ephemeris change, and
 * registering the Earth body group as the position registry's world frame.
 *
 * World-frame layers (trails, all-orbits, selection ring, labels) are scene-root children;
 * earth-local layers (Earth, sats, GS, links, flows, footprint, ground tracks) are children
 * of <Body>. Mounted only behind the `?r3f` flag until parity + cutover.
 */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { onSnapshot, setPlaybackPaused } from "../../sim/simClock";
import {
  destroyWorkerBridge,
  initWorkerBridge,
  requestFlush,
  sendEphemeris,
} from "../../sim/workerBridge";
import type { SessionEphemeris } from "../../sim/ephemeris";
import type { ColorMode, GlobeMode, ReferenceFrame, Selection, StateSnapshot } from "../../types";
import { Universe } from "./Universe";
import { Body } from "./Body";
import { Earth, Starfield } from "./Earth";
import { Constellation } from "./Constellation";
import { GroundStations } from "./GroundStation";
import { GroundTracks } from "./GroundTracks";
import { Links } from "./Links";
import { FlowPaths } from "./FlowPaths";
import { CoverageFootprint } from "./CoverageFootprint";
import { Trails } from "./Trails";
import { AllOrbits } from "./AllOrbits";
import { Labels } from "./Labels";
import { SelectionOverlay } from "./SelectionOverlay";
import { FrameDriver } from "./FrameDriver";
import { setEarthFrame } from "./positions";
import { EARTH_RADIUS_KM } from "./units";

interface SceneProps {
  snapshot: StateSnapshot | null;
  ephemeris: SessionEphemeris | null;
  colorMode: ColorMode;
  globeMode: GlobeMode;
  referenceFrame: ReferenceFrame;
  playbackPaused: boolean;
  showIslLinks: boolean;
  showGroundLinks: boolean;
  showSatPaths: boolean;
  showTrails: boolean;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
}

export function Scene({
  snapshot,
  ephemeris,
  colorMode,
  globeMode,
  referenceFrame,
  playbackPaused,
  showIslLinks,
  showGroundLinks,
  showSatPaths,
  showTrails,
  selection,
  onSelect,
}: SceneProps) {
  const earthGroupRef = useRef<THREE.Group>(null);
  const starGroupRef = useRef<THREE.Group>(null);
  const labelContainerRef = useRef<HTMLDivElement>(null);

  // Register the Earth body group as the world frame for the position registry.
  useEffect(() => {
    setEarthFrame(earthGroupRef.current);
    return () => setEarthFrame(null);
  }, []);

  // Feed the shared EMA clock on each snapshot (drives propagation timing + interpolation).
  useEffect(() => {
    if (snapshot) onSnapshot(snapshot.sim_time, performance.now());
  }, [snapshot]);

  // Freeze/unfreeze the clock on pause (R-OME-008B: d(sim)/d(wall) = 0 when paused).
  useEffect(() => {
    setPlaybackPaused(playbackPaused);
  }, [playbackPaused]);

  // Drive the SGP4 worker on ephemeris change (mirrors GlobeView). The Constellation reads
  // worker positions, falling back to main-thread propagation when the worker is unavailable.
  useEffect(() => {
    if (!ephemeris) return;
    initWorkerBridge();
    sendEphemeris(ephemeris);
    requestFlush(new Date(ephemeris.sim_time).getTime() / 1000, 1.0);
  }, [ephemeris]);

  useEffect(() => () => destroyWorkerBridge(), []);

  const nodes = snapshot?.nodes ?? [];

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <Universe>
        <FrameDriver
          earthFrame={earthGroupRef}
          starFrame={starGroupRef}
          referenceFrame={referenceFrame}
        />
        <group ref={starGroupRef} name="starFrame">
          <Starfield />
        </group>
        {/* World-frame trails + full-constellation orbit rings (scene-root). */}
        <Trails enabled={showTrails} nodes={nodes} resetKey={ephemeris?.epoch_id} />
        <AllOrbits
          nodes={nodes}
          show={showSatPaths}
          earthFrame={earthGroupRef}
          referenceFrame={referenceFrame}
        />
        <Body id="earth" radiusKm={EARTH_RADIUS_KM} ref={earthGroupRef}>
          <Earth globeMode={globeMode} simTimeIso={snapshot?.sim_time ?? null} />
          <Constellation
            nodes={nodes}
            ephemeris={ephemeris}
            colorMode={colorMode}
            onSelect={onSelect}
          />
          <GroundStations nodes={nodes} selection={selection} onSelect={onSelect} />
          <GroundTracks nodes={nodes} enabled={false} />
          <Links
            links={snapshot?.links ?? []}
            kernelActualPairs={snapshot?.kernel_actual_pairs ?? []}
            showIslLinks={showIslLinks}
            showGroundLinks={showGroundLinks}
          />
          <FlowPaths tracedPaths={snapshot?.traced_paths ?? []} />
          <CoverageFootprint selection={selection} nodes={nodes} />
        </Body>
        <SelectionOverlay selection={selection} />
        <Labels
          nodes={nodes}
          satLabelsEnabled
          gsLabelsEnabled
          containerRef={labelContainerRef}
        />
      </Universe>
      {/* HTML label overlay — Labels (inside the canvas) projects positions into these divs. */}
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
