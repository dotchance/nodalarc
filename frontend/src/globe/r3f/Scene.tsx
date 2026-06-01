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
 * of <Body>. R3F is the single production globe implementation.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import * as THREE from "three";
import type { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { onSnapshot, setPlaybackPaused, resetSimClock } from "../../sim/simClock";
import { useDecisionExplanation } from "../../explain/useDecisionExplanation";
import { useGroundCandidates } from "../../explain/useGroundCandidates";
import { gsCandidateRelations } from "../../explain/gsCandidateRelations";
import {
  destroyWorkerBridge,
  initWorkerBridge,
  requestFlush,
  sendEphemeris,
} from "../../sim/workerBridge";
import type { PlaybackStateMsg, SessionEphemeris } from "../../sim/ephemeris";
import type { ColorMode, GlobeMode, ReferenceFrame, Selection, StateSnapshot } from "../../types";
import type { GlobeActions } from "../actions";
import { Universe } from "./Universe";
import { GlobeActionsBridge } from "./GlobeActionsBridge";
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
import { OrbitPins } from "./OrbitPins";
import { LinkPicker } from "./LinkPicker";
import { Labels } from "./Labels";
import { Tooltip, type HoverInfo } from "./Tooltip";
import { SelectionOverlay } from "./SelectionOverlay";
import { FrameDriver } from "./FrameDriver";
import { EARTH_RADIUS_KM } from "./units";

const MAX_PINS = 7;

interface SceneProps {
  snapshot: StateSnapshot | null;
  ephemeris: SessionEphemeris | null;
  colorMode: ColorMode;
  globeMode: GlobeMode;
  referenceFrame: ReferenceFrame;
  playbackPaused: boolean;
  playbackState: PlaybackStateMsg | null;
  showIslLinks: boolean;
  showGroundLinks: boolean;
  showSatPaths: boolean;
  showTrails: boolean;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  /** Imperative camera/screenshot handle the rest of the app drives (Toolbar, keyboard, fly-to). */
  actionsRef?: MutableRefObject<GlobeActions | null>;
}

export function Scene({
  snapshot,
  ephemeris,
  colorMode,
  globeMode,
  referenceFrame,
  playbackPaused,
  playbackState,
  showIslLinks,
  showGroundLinks,
  showSatPaths,
  showTrails,
  selection,
  onSelect,
  actionsRef,
}: SceneProps) {
  // The Earth body group, used by FrameDriver / AllOrbits / OrbitPins to drive its view-frame
  // rotation. <Body> populates this ref AND self-registers its frame in the position registry
  // (setBodyFrame, via its own callback ref) the moment the group attaches post-Suspense.
  const earthGroupRef = useRef<THREE.Group>(null);
  const starGroupRef = useRef<THREE.Group>(null);
  const labelContainerRef = useRef<HTMLDivElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  // The LinkPicker (inside the Canvas) publishes its hit-test-and-select here; the Canvas-level
  // onPointerMissed below invokes it on a background click. No-op until the LinkPicker mounts.
  const missedRef = useRef<(event: MouseEvent) => void>(() => {});

  // ctrl/cmd-click orbit pins (capped, oldest evicted) + hover tooltip state.
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const togglePin = useCallback((id: string) => {
    setPinnedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id].slice(-MAX_PINS),
    );
  }, []);

  // Session/constellation switch reset (the legacy GlobeView did this in one block): drop the
  // pins (they reference the old constellation's sats) and reset the EMA sim-clock so the prior
  // session's smoothed rate does not bleed into the new one. Trails + link batch reset
  // declaratively via their resetKeys below; orbit rings re-seed on their own.
  const constellation = snapshot?.constellation_name ?? null;
  const nodes = snapshot?.nodes ?? [];

  // On-select decision data, lifted here so the globe is internally single-sourced: the GS
  // envelope cone and the per-sat relation tinting both read from
  // ONE decision-explanation + ONE ground-candidates fetch for the selected GS — and from the SAME
  // client + classifier the node card uses, so a satellite never reads one family on the glyph and
  // another in the panel. Only fetched when a GS is selected (selectedGsId null otherwise).
  const selectedGsId = selection?.type === "ground_station" ? selection.id : null;
  const simTime = snapshot?.sim_time;
  const facts = useDecisionExplanation(selectedGsId, null, simTime).facts;
  const envelope = facts?.envelope ?? null;
  const candidates = useGroundCandidates(selectedGsId, simTime).data;
  const relations = useMemo(
    () =>
      selectedGsId ? gsCandidateRelations(selectedGsId, candidates, snapshot?.links ?? []) : null,
    [selectedGsId, candidates, snapshot?.links],
  );
  useEffect(() => {
    setPinnedIds([]);
    resetSimClock();
  }, [constellation]);

  // Prune pins for satellites that have left the constellation mid-session, so a stale id never
  // lingers (legacy reclaimed the instance slot every frame). Same-ref return = no re-render.
  useEffect(() => {
    setPinnedIds((prev) => {
      if (prev.length === 0) return prev;
      const live = new Set(
        nodes.filter((n) => n.node_type === "satellite").map((n) => n.node_id),
      );
      const next = prev.filter((id) => live.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [nodes]);

  // Epoch-suspension overlay (PRD seek protocol). "seeking" shows the overlay; only "playing"
  // clears it ("paused" leaves it as-is) — legacy GlobeView verbatim. Trails flush on seek is
  // handled by the resetKey below (a seek resets the epoch, so epoch_id changes).
  const [seeking, setSeeking] = useState(false);
  useEffect(() => {
    if (playbackState?.state === "seeking") setSeeking(true);
    else if (playbackState?.state === "playing") setSeeking(false);
  }, [playbackState]);

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

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <Universe
        controlsRef={controlsRef}
        onPointerMissed={(e) => missedRef.current(e)}
        afterControls={<Labels nodes={nodes} containerRef={labelContainerRef} />}
      >
        {actionsRef && (
          <GlobeActionsBridge actionsRef={actionsRef} controlsRef={controlsRef} />
        )}
        {/* Click a beam to select the link; click empty space / Earth to deselect (legacy
            gpuPicker link path + onSelect(null)-on-miss). Sats/GS are picked by their own handlers. */}
        <LinkPicker
          links={snapshot?.links ?? []}
          showIslLinks={showIslLinks}
          showGroundLinks={showGroundLinks}
          onSelect={onSelect}
          handlerRef={missedRef}
        />
        <FrameDriver
          earthFrame={earthGroupRef}
          starFrame={starGroupRef}
          referenceFrame={referenceFrame}
        />
        <group ref={starGroupRef} name="starFrame">
          <Starfield />
        </group>
        {/* World-frame trails + full-constellation orbit rings (scene-root). Trail history is
            world-space + session-scoped, so flush it on epoch change, reference-frame toggle, AND
            constellation switch — mixing points from two frames or two constellations is
            meaningless (legacy flushTrails on each of those). epoch_id stays 0 across a plain
            switch, so constellation must be in the key in its own right. */}
        <Trails
          enabled={showTrails}
          nodes={nodes}
          resetKey={`${ephemeris?.epoch_id ?? "none"}|${referenceFrame}|${constellation ?? "none"}`}
        />
        <AllOrbits
          nodes={nodes}
          show={showSatPaths}
          earthFrame={earthGroupRef}
          referenceFrame={referenceFrame}
        />
        <OrbitPins
          pinnedIds={pinnedIds}
          nodes={nodes}
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
            onTogglePin={togglePin}
            onHover={setHover}
            relations={relations}
          />
          <GroundStations
            nodes={nodes}
            selection={selection}
            links={snapshot?.links ?? []}
            actuationNotices={snapshot?.actuation_notices ?? []}
            envelope={envelope}
            onSelect={onSelect}
            onHover={setHover}
          />
          <GroundTracks nodes={nodes} enabled={false} />
          <Links
            links={snapshot?.links ?? []}
            kernelActualPairs={snapshot?.kernel_actual_pairs ?? []}
            showIslLinks={showIslLinks}
            showGroundLinks={showGroundLinks}
            resetKey={constellation ?? "none"}
          />
          <FlowPaths tracedPaths={snapshot?.traced_paths ?? []} />
          <CoverageFootprint selection={selection} nodes={nodes} />
        </Body>
        <SelectionOverlay selection={selection} />
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
      <Tooltip hover={hover} />
      {/* Epoch-suspension overlay during a seek (PRD seek protocol) — legacy "Recalculating Epoch". */}
      {seeking && (
        <div
          style={{
            position: "absolute",
            inset: 0,
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
