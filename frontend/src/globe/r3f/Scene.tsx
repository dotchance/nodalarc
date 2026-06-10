// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The R3F scene root and orchestrator. Composes the full declarative scene — Earth +
 * starfield, instanced constellation, ground stations, links/beams, flow paths, coverage
 * footprint, orbital trails, full-constellation orbit rings, ground tracks, the selection
 * overlay, and the HTML label layer — and drives the reference-frame rotation (FrameDriver).
 * It owns the cross-cutting lifecycle the legacy GlobeView held: feeding the EMA sim-clock
 * per snapshot, pausing the clock, driving the SGP4 worker on ephemeris change, and
 * registering each active body group as a position-registry frame.
 *
 * World-frame layers (links, flows, trails, all-orbits, selection ring, labels) are scene-root
 * children; body-local layers (planet/moon appearance, sats, GS, footprint, ground tracks) are
 * children of that body's <Body>. R3F is the single production globe implementation.
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
import {
  kmPerRenderUnitFromEphemeris,
  type EphemerisBodyFrame,
  type PlaybackStateMsg,
  type SessionEphemeris,
} from "../../sim/ephemeris";
import type { ColorMode, GlobeMode, ReferenceFrame, Selection, StateSnapshot } from "../../types";
import type { GlobeActions } from "../actions";
import { Universe } from "./Universe";
import { GlobeActionsBridge } from "./GlobeActionsBridge";
import { Body } from "./Body";
import { Earth, Moon, Starfield } from "./Earth";
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
import { kmToRender } from "./units";
import {
  cameraDistanceForSceneRadius,
  cameraFarForMaxDistance,
  sceneFrameForCamera,
  sceneRadiusForCamera,
} from "./cameraBounds";

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
  // The screen-space picker (inside the Canvas) publishes its hit-test-and-select here; the
  // Canvas-level onPointerMissed below invokes it when physical geometry is missed. No-op until
  // the picker mounts.
  const missedRef = useRef<(event: MouseEvent) => void>(() => {});

  // ctrl/cmd-click orbit pins (capped, oldest evicted) + hover tooltip state.
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [cameraFocusLabel, setCameraFocusLabel] = useState("Scene");
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
  const earthNodes = useMemo(
    () => nodes.filter((node) => node.reference_body === "earth"),
    [nodes],
  );
  const simTimeUnix = snapshot?.sim_time ? Date.parse(snapshot.sim_time) / 1000 : null;
  const kmPerRenderUnit = useMemo(
    () => (ephemeris ? kmPerRenderUnitFromEphemeris(ephemeris) : null),
    [ephemeris],
  );
  const earthRotationRateRadS = ephemeris?.body_frames?.earth?.rotation_rate_rad_s ?? null;
  const bodies = useMemo(() => {
    if (!ephemeris || kmPerRenderUnit === null) return [];
    const frames = ephemeris.body_frames;
    const ids = new Set<string>(Object.keys(frames));
    for (const node of nodes) {
      if (!node.reference_body) {
        throw new Error(`Node ${node.node_id} is missing reference_body`);
      }
      ids.add(node.reference_body);
    }

    return [...ids]
      .sort((a, b) => (a === "earth" ? -1 : b === "earth" ? 1 : a.localeCompare(b)))
      .map((id) => {
        const frame = frames[id];
        if (!frame) {
          throw new Error(`SessionEphemeris missing body frame for rendered body ${id}`);
        }
        return {
          id,
          radiusKm: frame.equatorial_radius_km,
          position: bodyFramePosition(
            frame,
            ephemeris?.epoch_unix ?? null,
            simTimeUnix,
            kmPerRenderUnit,
          ),
        };
      })
      .filter((value): value is { id: string; radiusKm: number; position: [number, number, number] } =>
        value !== null,
      );
  }, [ephemeris, nodes, simTimeUnix, kmPerRenderUnit]);
  const controlsMaxDistance = useMemo(
    () =>
      cameraDistanceForSceneRadius(
        sceneRadiusForCamera(bodies, nodes, kmPerRenderUnit),
      ),
    [bodies, nodes, kmPerRenderUnit],
  );
  const sceneFrame = useMemo(
    () => sceneFrameForCamera(bodies, nodes, kmPerRenderUnit),
    [bodies, nodes, kmPerRenderUnit],
  );
  const cameraFar = useMemo(
    () => cameraFarForMaxDistance(controlsMaxDistance),
    [controlsMaxDistance],
  );
  const bodyPickTargets = useMemo(
    () =>
      bodies.map((body) => ({
        id: body.id,
        center: new THREE.Vector3(body.position[0], body.position[1], body.position[2]),
        radius:
          kmPerRenderUnit === null
            ? 0
            : kmToRender(body.radiusKm, kmPerRenderUnit),
      })),
    [bodies, kmPerRenderUnit],
  );

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
  const focusNode = useCallback(
    (nodeId: string) => {
      actionsRef?.current?.focusNode(nodeId);
    },
    [actionsRef],
  );
  const focusLink = useCallback(
    (nodeA: string, nodeB: string) => {
      actionsRef?.current?.focusLink(nodeA, nodeB);
    },
    [actionsRef],
  );
  const focusBody = useCallback(
    (bodyId: string) => {
      actionsRef?.current?.focusBody(bodyId);
    },
    [actionsRef],
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
        controlsMaxDistance={controlsMaxDistance}
        cameraFar={cameraFar}
        afterControls={
          <Labels
            nodes={nodes}
            containerRef={labelContainerRef}
            selectedGsId={selection?.type === "ground_station" ? selection.id : null}
          />
        }
      >
        {actionsRef && (
          <GlobeActionsBridge
            actionsRef={actionsRef}
            controlsRef={controlsRef}
            sceneFitDistance={controlsMaxDistance}
            sceneFrame={sceneFrame}
            onFocusChange={setCameraFocusLabel}
          />
        )}
        {/* Missed physical hits get a screen-space pass: nodes first, then beams, then bodies.
            This keeps MEO/GEO/cislunar objects usable without visually inflating the scene. */}
        <LinkPicker
          nodes={nodes}
          links={snapshot?.links ?? []}
          bodies={bodyPickTargets}
          showIslLinks={showIslLinks}
          showGroundLinks={showGroundLinks}
          onSelect={onSelect}
          onFocusNode={focusNode}
          onFocusLink={focusLink}
          onFocusBody={focusBody}
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
        {ephemeris !== null && kmPerRenderUnit !== null && earthRotationRateRadS !== null && (
          <AllOrbits
            nodes={earthNodes}
            show={showSatPaths}
            earthFrame={earthGroupRef}
            referenceFrame={referenceFrame}
            kmPerRenderUnit={kmPerRenderUnit}
            earthRotationRateRadS={earthRotationRateRadS}
            ephemeris={ephemeris}
          />
        )}
        {ephemeris !== null && kmPerRenderUnit !== null && earthRotationRateRadS !== null && (
          <OrbitPins
            pinnedIds={pinnedIds}
            nodes={earthNodes}
            earthFrame={earthGroupRef}
            referenceFrame={referenceFrame}
            kmPerRenderUnit={kmPerRenderUnit}
            earthRotationRateRadS={earthRotationRateRadS}
            ephemeris={ephemeris}
          />
        )}
        <Links
          links={snapshot?.links ?? []}
          kernelActualPairs={snapshot?.kernel_actual_pairs ?? []}
          showIslLinks={showIslLinks}
          showGroundLinks={showGroundLinks}
          resetKey={constellation ?? "none"}
        />
        <FlowPaths tracedPaths={snapshot?.traced_paths ?? []} />
        {kmPerRenderUnit !== null && bodies.map((body) => {
          const bodyNodes = nodes.filter((node) => node.reference_body === body.id);
          return (
            <Body
              key={body.id}
              id={body.id}
              radiusKm={body.radiusKm}
              kmPerRenderUnit={kmPerRenderUnit}
              position={body.position}
              onFocusBody={focusBody}
              ref={body.id === "earth" ? earthGroupRef : undefined}
            >
              {body.id === "earth" ? (
                <Earth globeMode={globeMode} simTimeIso={snapshot?.sim_time ?? null} />
              ) : body.id === "luna" ? (
                <Moon />
              ) : null}
              <Constellation
                nodes={bodyNodes}
                ephemeris={ephemeris}
                colorMode={colorMode}
                onSelect={onSelect}
                onFocusNode={focusNode}
                onTogglePin={togglePin}
                onHover={setHover}
                relations={relations}
              />
              <GroundStations
                nodes={bodyNodes}
                selection={selection}
                links={snapshot?.links ?? []}
                actuationNotices={snapshot?.actuation_notices ?? []}
                envelope={envelope}
                onSelect={onSelect}
                onFocusNode={focusNode}
                onHover={setHover}
              />
              <GroundTracks nodes={bodyNodes} enabled={false} />
              <CoverageFootprint selection={selection} nodes={bodyNodes} />
            </Body>
          );
        })}
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
      <div
        style={{
          position: "absolute",
          left: 90,
          top: 14,
          zIndex: 8,
          padding: "4px 8px",
          border: "1px solid rgba(90, 124, 255, 0.35)",
          borderRadius: 4,
          background: "rgba(12, 14, 28, 0.72)",
          color: "#aeb8ff",
          fontSize: 11,
          fontFamily: "monospace",
          pointerEvents: "none",
          maxWidth: "min(520px, calc(100vw - 180px))",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        Focus: {cameraFocusLabel}
      </div>
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

function bodyFramePosition(
  frame: EphemerisBodyFrame | undefined,
  epochUnix: number | null,
  simTimeUnix: number | null,
  kmPerRenderUnit: number,
): [number, number, number] {
  if (!frame) return [0, 0, 0];
  const dt = epochUnix !== null && simTimeUnix !== null ? simTimeUnix - epochUnix : 0;
  const xKm = frame.origin_x_km + frame.vel_x_km_s * dt;
  const yKm = frame.origin_y_km + frame.vel_y_km_s * dt;
  const zKm = frame.origin_z_km + frame.vel_z_km_s * dt;
  return [
    kmToRender(xKm, kmPerRenderUnit),
    kmToRender(zKm, kmPerRenderUnit),
    -kmToRender(yKm, kmPerRenderUnit),
  ];
}
