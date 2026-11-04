// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * The R3F scene root and orchestrator. Composes the full declarative scene — Earth +
 * starfield, instanced constellation, ground stations, links/beams, flow paths, coverage
 * footprint, orbital trails, full-constellation orbit rings, ground tracks, the selection
 * overlay, and the HTML label layer — and drives body placement and the reference frame
 * from OME's body samples (FrameDriver). It owns the cross-cutting lifecycle the legacy
 * GlobeView held: feeding the EMA sim-clock per snapshot, pausing the clock, feeding the
 * body-sample store with each SessionEphemeris and BodySamples window, driving the
 * propagation worker on ephemeris change, and registering each active body group as a
 * position-registry frame. Without body samples for the render time it shows that state
 * and draws no world.
 *
 * World-frame layers (links, flows, trails, all-orbits, selection ring, labels) are scene-root
 * children; body-local layers (planet/moon appearance, sats, GS, footprint, ground tracks) are
 * children of that body's <Body>. R3F is the single production globe implementation.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import * as THREE from "three";
import type { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { onSnapshot, setPlaybackPaused, resetSimClock } from "../../sim/simClock";
import { peekSkyAt } from "../../sim/bodySamples";
import { useBodySampleStore } from "./useBodySampleStore";
import { useDecisionExplanation } from "../../explain/useDecisionExplanation";
import { useGroundCandidates } from "../../explain/useGroundCandidates";
import { gsCandidateRelations } from "../../explain/gsCandidateRelations";
import {
  destroyWorkerBridge,
  initWorkerBridge,
  requestFlush,
  sendEphemeris,
} from "../../sim/workerBridge";
import { kmPerRenderUnitFromEphemeris } from "../../sim/ephemeris";
import type { ColorMode, GlobeMode, ReferenceFrame, Selection } from "../../types";
import type { GlobeActions } from "../actions";
import { isGroundNode } from "../../networkIdentity";
import { Universe } from "./Universe";
import { GlobeActionsBridge } from "./GlobeActionsBridge";
import { Body } from "./Body";
import { Earth, Mars, Moon, Starfield, SunLight } from "./Earth";
import { Constellation, satColor } from "./Constellation";
import type { AreaColoring } from "../../routing/instances";
import { GroundStations } from "./GroundStation";
import { GroundTracks } from "./GroundTracks";
import type { Regime } from "../../taxonomy/regime";
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
import { FrameDriver, type SkyAvailability } from "./FrameDriver";
import { placedBodiesAt } from "./bodyPose";
import { kmToRender } from "./units";
import {
  cameraDistanceForSceneRadius,
  cameraFarForMaxDistance,
  sceneFrameForCamera,
  sceneRadiusForCamera,
} from "./cameraBounds";
import type { NodeState, StateSnapshot } from "../../generated/vsApi";
import type {
  BodySamples,
  SessionEphemeris,
  SupportedSurfaceBody,
} from "../../builder/generated/builderApi";

/** Each supported body's surface, keyed by every body the backend supports: a supported body
 *  without a surface fails the type check. */
const BODY_SURFACE: Record<SupportedSurfaceBody, (props: { globeMode: GlobeMode }) => React.ReactNode> = {
  earth: ({ globeMode }) => <Earth globeMode={globeMode} />,
  luna: () => <Moon />,
  mars: () => <Mars />,
};

function BodySurface({ id, globeMode }: { id: string; globeMode: GlobeMode }) {
  const surface = BODY_SURFACE[id as SupportedSurfaceBody];
  if (surface === undefined) {
    throw new Error(`no surface for body ${id}: the page supports ${Object.keys(BODY_SURFACE).join(", ")}`);
  }
  return <>{surface({ globeMode })}</>;
}

const MAX_PINS = 7;

/** The state fields the scene renders. The live feed passes its frame; the
 *  builder derives these from a resolved world. */
export type SceneSnapshot = Pick<
  StateSnapshot,
  | "sim_time"
  | "constellation_name"
  | "nodes"
  | "links"
  | "kernel_actual_pairs"
  | "traced_paths"
  | "actuation_notices"
  | "playback_speed"
  | "playback_achieved"
>;

interface SceneProps {
  snapshot: SceneSnapshot | null;
  /** When the snapshot arrived (performance.now() at decode); anchors the clock phase. */
  snapshotArrivalMs: number | null;
  ephemeris: SessionEphemeris | null;
  /** The latest body-sample window received; the store decides what is displayable. */
  bodySamples: BodySamples | null;
  colorMode: ColorMode;
  globeMode: GlobeMode;
  referenceFrame: ReferenceFrame;
  /** Null until a snapshot states it. */
  playbackPaused: boolean | null;
  showIslLinks: boolean;
  showGroundLinks: boolean;
  showSatPaths: boolean;
  showGroundTracks: boolean;
  regimeById: ReadonlyMap<string, Regime>;
  /** Area colors of the chosen IS-IS or OSPF instance. */
  areaColoring: AreaColoring;
  showTrails: boolean;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  /** Imperative camera/screenshot handle the rest of the app drives (Toolbar, keyboard, fly-to). */
  actionsRef?: MutableRefObject<GlobeActions | null>;
  /** Ground-selection decision explanation (envelope cone + candidate tinting)
   *  answers for the running session's scheduler. Surfaces rendering a world
   *  that is not the running session (the session builder) disable it so the
   *  scene never explains one world with another world's decisions. */
  liveExplain?: boolean;
  /** Extra world-frame layers composed by the hosting surface (domain
   *  overlays per the viewer layering — e.g. the builder's candidate lines).
   *  Rendered at scene root alongside links/flows. */
  worldLayers?: React.ReactNode;
}

export function Scene({
  snapshot,
  snapshotArrivalMs,
  ephemeris,
  bodySamples,
  colorMode,
  globeMode,
  referenceFrame,
  playbackPaused,
  showIslLinks,
  showGroundLinks,
  showSatPaths,
  showGroundTracks,
  regimeById,
  areaColoring,
  showTrails,
  selection,
  onSelect,
  actionsRef,
  liveExplain = true,
  worldLayers,
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
  const trackColorOf = useCallback(
    (node: NodeState) => satColor(node, colorMode, regimeById.get(node.node_id), areaColoring),
    [colorMode, regimeById, areaColoring],
  );
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
  const groundNodeIds = useMemo(
    () => new Set(nodes.filter(isGroundNode).map((node) => node.node_id)),
    [nodes],
  );
  const earthNodes = useMemo(
    () => nodes.filter((node) => node.reference_body === "earth"),
    [nodes],
  );
  const simTimeUnix = snapshot?.sim_time ? Date.parse(snapshot.sim_time) / 1000 : null;
  const kmPerRenderUnit = useMemo(
    () => (ephemeris ? kmPerRenderUnitFromEphemeris(ephemeris) : null),
    [ephemeris],
  );
  // This scene owns the body-sample store while mounted; its version changes whenever the
  // accepted samples do.
  const bodySamplesVersion = useBodySampleStore(ephemeris, bodySamples);
  const [skyAvailability, setSkyAvailability] = useState<SkyAvailability>("waiting-for-clock");
  const skyAvailable = skyAvailability === "available";

  const bodies = useMemo(() => {
    if (!ephemeris || kmPerRenderUnit === null) return [];
    const frames = ephemeris.body_frames;
    const ids = new Set<SupportedSurfaceBody>();
    for (const frame of Object.values(frames)) if (frame) ids.add(frame.body_id);
    for (const node of nodes) ids.add(node.reference_body);

    return [...ids]
      .sort((a, b) => (a === "earth" ? -1 : b === "earth" ? 1 : a.localeCompare(b)))
      .map((id) => {
        const frame = frames[id];
        if (!frame) {
          throw new Error(`SessionEphemeris missing body frame for rendered body ${id}`);
        }
        return { id, radiusKm: frame.equatorial_radius_km };
      });
  }, [ephemeris, nodes, kmPerRenderUnit]);
  const bodyIds = useMemo(() => bodies.map((body) => body.id), [bodies]);
  // The render origin: the body the world is centred on (bodyPose.ts says why). A focus asks
  // for its body; until one does, and when the asked body is not in this session, the world
  // is centred on the first body (Earth when the session has it).
  const [requestedOrigin, setRequestedOrigin] = useState<string | null>(null);
  const originBody =
    bodies.find((body) => body.id === requestedOrigin) ?? bodies[0] ?? null;
  const originBodyId = originBody?.id ?? null;
  // The controls stop 5 % above the origin body's surface radius (105 units at Earth).
  // Before the session's bodies are known nothing is drawn, so nothing bounds the approach.
  const controlsMinDistance =
    originBody === null || kmPerRenderUnit === null
      ? 0
      : kmToRender(originBody.radiusKm, kmPerRenderUnit) * 1.05;
  // Body centres at the snapshot's sim time, for camera framing and picking. A body
  // with no sample covering that time has no centre to frame or pick. Read without releasing
  // windows (a render must not change the store); recomputed when the accepted samples change.
  const placedBodies = useMemo(
    () =>
      simTimeUnix === null || kmPerRenderUnit === null || originBodyId === null
        ? []
        : placedBodiesAt(
            bodies,
            peekSkyAt(simTimeUnix),
            referenceFrame,
            kmPerRenderUnit,
            originBodyId,
          ),
    [bodies, simTimeUnix, kmPerRenderUnit, referenceFrame, originBodyId, bodySamplesVersion],
  );
  const controlsMaxDistance = useMemo(
    () =>
      cameraDistanceForSceneRadius(
        sceneRadiusForCamera(placedBodies, nodes, kmPerRenderUnit),
      ),
    [placedBodies, nodes, kmPerRenderUnit],
  );
  const sceneFrame = useMemo(
    () => sceneFrameForCamera(placedBodies, nodes, kmPerRenderUnit),
    [placedBodies, nodes, kmPerRenderUnit],
  );
  const cameraFar = useMemo(
    () => cameraFarForMaxDistance(controlsMaxDistance),
    [controlsMaxDistance],
  );
  const bodyPickTargets = useMemo(
    () =>
      placedBodies.map((body) => ({
        id: body.id,
        center: new THREE.Vector3(body.position[0], body.position[1], body.position[2]),
        radius:
          kmPerRenderUnit === null
            ? 0
            : kmToRender(body.radiusKm, kmPerRenderUnit),
      })),
    [placedBodies, kmPerRenderUnit],
  );

  // On-select decision data, lifted here so the globe is internally single-sourced: the GS
  // envelope cone and the per-sat relation tinting both read from
  // one decision-explanation + one ground-candidates fetch for the selected GS — and from the same
  // client + classifier the node card uses, so a satellite never reads one family on the glyph and
  // another in the panel. Only fetched when a GS is selected (selectedGsId null otherwise).
  const selectedGsId =
    liveExplain && selection?.type === "ground_station" ? selection.id : null;
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

  // Feed the shared clock on each snapshot (drives propagation timing +
  // interpolation). Phase anchors at transport arrival (stamped at WS
  // decode), not at effect execution — render scheduling jitter must not
  // enter the clock.
  useEffect(() => {
    if (snapshot && snapshotArrivalMs !== null)
      onSnapshot(
        snapshot.sim_time,
        snapshotArrivalMs,
        snapshot.playback_achieved ?? snapshot.playback_speed,
      );
  }, [snapshot, snapshotArrivalMs]);

  // Freeze/unfreeze the clock on pause; d(sim)/d(wall) is 0 while paused.
  useEffect(() => {
    if (playbackPaused !== null) setPlaybackPaused(playbackPaused);
  }, [playbackPaused]);

  // Drive the Keplerian/J2 worker on ephemeris change. The Constellation reads worker
  // positions, falls back to main-thread propagation, and uses snapshots for TLE nodes.
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
        controlsMinDistance={controlsMinDistance}
        controlsMaxDistance={controlsMaxDistance}
        cameraFar={cameraFar}
        afterControls={
          skyAvailable ? (
            <Labels
              nodes={nodes}
              containerRef={labelContainerRef}
              selectedGsId={selection?.type === "ground_station" ? selection.id : null}
              selectedNodeId={selection?.id ?? null}
            />
          ) : null
        }
      >
        {actionsRef && (
          <GlobeActionsBridge
            actionsRef={actionsRef}
            controlsRef={controlsRef}
            sceneFitDistance={controlsMaxDistance}
            sceneFrame={sceneFrame}
            minDistance={controlsMinDistance}
            onOriginRequest={setRequestedOrigin}
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
        {kmPerRenderUnit !== null && originBodyId !== null && (
          <FrameDriver
            starFrame={starGroupRef}
            referenceFrame={referenceFrame}
            kmPerRenderUnit={kmPerRenderUnit}
            bodyIds={bodyIds}
            originBodyId={originBodyId}
            onAvailabilityChange={setSkyAvailability}
          />
        )}
        <SunLight
          referenceFrame={referenceFrame}
          kmPerRenderUnit={kmPerRenderUnit}
          originBodyId={originBodyId}
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
          enabled={showTrails && skyAvailable}
          nodes={nodes}
          resetKey={`${ephemeris?.epoch_id ?? "none"}|${referenceFrame}|${constellation ?? "none"}`}
        />
        {skyAvailable && ephemeris !== null && kmPerRenderUnit !== null && (
          <AllOrbits
            nodes={earthNodes}
            show={showSatPaths}
            earthFrame={earthGroupRef}
            referenceFrame={referenceFrame}
            kmPerRenderUnit={kmPerRenderUnit}
            ephemeris={ephemeris}
          />
        )}
        {skyAvailable && ephemeris !== null && kmPerRenderUnit !== null && (
          <OrbitPins
            pinnedIds={pinnedIds}
            nodes={earthNodes}
            earthFrame={earthGroupRef}
            referenceFrame={referenceFrame}
            kmPerRenderUnit={kmPerRenderUnit}
            ephemeris={ephemeris}
          />
        )}
        {/* World layers read node positions through the body groups; without body samples
            for the render time there is nothing true to draw them from. */}
        {skyAvailable && (
          <>
            <Links
              links={snapshot?.links ?? []}
              kernelActualPairs={snapshot?.kernel_actual_pairs ?? []}
              showIslLinks={showIslLinks}
              showGroundLinks={showGroundLinks}
              resetKey={constellation ?? "none"}
            />
            <FlowPaths tracedPaths={snapshot?.traced_paths ?? []} groundNodeIds={groundNodeIds} />
            {worldLayers}
          </>
        )}
        {kmPerRenderUnit !== null && bodies.map((body) => {
          const bodyNodes = nodes.filter((node) => node.reference_body === body.id);
          return (
            <Body
              key={body.id}
              id={body.id}
              radiusKm={body.radiusKm}
              kmPerRenderUnit={kmPerRenderUnit}
              visible={skyAvailable}
              onFocusBody={focusBody}
              ref={body.id === "earth" ? earthGroupRef : undefined}
            >
              <BodySurface id={body.id} globeMode={globeMode} />
              <Constellation
                nodes={bodyNodes}
                ephemeris={ephemeris}
                colorMode={colorMode}
                onSelect={onSelect}
                onFocusNode={focusNode}
                onTogglePin={togglePin}
                onHover={setHover}
                relations={relations}
                regimeById={regimeById}
                areaColoring={areaColoring}
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
              <GroundTracks nodes={bodyNodes} enabled={showGroundTracks} colorOf={trackColorOf} />
              <CoverageFootprint selection={selection} nodes={bodyNodes} />
            </Body>
          );
        })}
        {skyAvailable && <SelectionOverlay selection={selection} />}
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
      <Tooltip hover={hover} regime={hover ? regimeById.get(hover.node.node_id) : undefined} />
      <div className="scene-hud">
        Focus: {cameraFocusLabel}
        {/* A view of the whole session and one per body. At interplanetary scale a body can
            be smaller than a pixel in the session view, so these name what a double-click on
            the body cannot reach. */}
        {actionsRef && bodies.length > 1 && (
          <span className="scene-hud-views">
            <button
              type="button"
              className={`scene-hud-view${cameraFocusLabel === "Scene" ? " scene-hud-view--active" : ""}`}
              onClick={() => actionsRef.current?.frameScene()}
              title="Frame the whole session"
            >
              Session
            </button>
            {bodyIds.map((id) => (
              <button
                key={id}
                type="button"
                className={`scene-hud-view${cameraFocusLabel === id ? " scene-hud-view--active" : ""}`}
                onClick={() => focusBody(id)}
                title={`Fly to ${id}`}
              >
                {id}
              </button>
            ))}
          </span>
        )}
      </div>
      {ephemeris !== null && !skyAvailable && (
        <div className="scene-unavailable" role="status">
          {SKY_AVAILABILITY_TEXT[skyAvailability]}
        </div>
      )}
    </div>
  );
}

const SKY_AVAILABILITY_TEXT: Record<SkyAvailability, string> = {
  available: "",
  "waiting-for-clock": "Waiting for the simulation clock.",
  "no-body-samples":
    "Body positions are unavailable: no body samples for this session epoch cover the simulation time.",
  "no-earth-for-fixed-view": "The Earth-fixed view needs Earth in the session.",
};
