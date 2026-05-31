// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The R3F scene root and orchestrator. Renders Earth + the inertial starfield (in its own
 * star frame group) + the instanced constellation, drives the reference-frame rotation
 * (FrameDriver), and owns the cross-cutting lifecycle the legacy GlobeView held: feeding
 * the EMA sim-clock per snapshot, pausing the clock, driving the SGP4 worker on ephemeris
 * change, and registering the Earth body group as the position registry's world frame.
 * Ground stations, beams, overlays, the selection layer, and trails/orbits are added in
 * later phases. Mounted only behind the `?r3f` flag — the imperative globe stays live until
 * this scene reaches parity.
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
import type { ColorMode, ReferenceFrame, Selection, StateSnapshot } from "../../types";
import { Universe } from "./Universe";
import { Body } from "./Body";
import { Earth, Starfield } from "./Earth";
import { Constellation } from "./Constellation";
import { GroundStations } from "./GroundStation";
import { Links } from "./Links";
import { SelectionOverlay } from "./SelectionOverlay";
import { FrameDriver } from "./FrameDriver";
import { setEarthFrame } from "./positions";
import { EARTH_RADIUS_KM } from "./units";

interface SceneProps {
  snapshot: StateSnapshot | null;
  ephemeris: SessionEphemeris | null;
  colorMode: ColorMode;
  referenceFrame: ReferenceFrame;
  playbackPaused: boolean;
  showIslLinks: boolean;
  showGroundLinks: boolean;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
}

export function Scene({
  snapshot,
  ephemeris,
  colorMode,
  referenceFrame,
  playbackPaused,
  showIslLinks,
  showGroundLinks,
  selection,
  onSelect,
}: SceneProps) {
  const earthGroupRef = useRef<THREE.Group>(null);
  const starGroupRef = useRef<THREE.Group>(null);

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
    <Universe>
      <FrameDriver
        earthFrame={earthGroupRef}
        starFrame={starGroupRef}
        referenceFrame={referenceFrame}
      />
      <group ref={starGroupRef} name="starFrame">
        <Starfield />
      </group>
      <Body id="earth" radiusKm={EARTH_RADIUS_KM} ref={earthGroupRef}>
        <Earth />
        <Constellation
          nodes={nodes}
          ephemeris={ephemeris}
          colorMode={colorMode}
          onSelect={onSelect}
        />
        <GroundStations nodes={nodes} selection={selection} onSelect={onSelect} />
        <Links
          links={snapshot?.links ?? []}
          kernelActualPairs={snapshot?.kernel_actual_pairs ?? []}
          showIslLinks={showIslLinks}
          showGroundLinks={showGroundLinks}
        />
      </Body>
      <SelectionOverlay selection={selection} />
    </Universe>
  );
}
