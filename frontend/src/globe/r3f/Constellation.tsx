// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Constellation — all satellites as one InstancedMesh (O(1) draw call at any scale).
 * The snapshot seeds instance slots + colors; every frame
 * each satellite's position is PROPAGATED client-side (the reused SGP4 worker, or the
 * main-thread propagateToSceneXYZ fallback) keyed on the EMA-interpolated sim time — NOT
 * interpolated between snapshot positions. The truth layer (worker, propagateToSceneXYZ,
 * simClock, geoToWorld) is reused verbatim; only the three.js object lifecycle is
 * reimplemented declaratively. Positions are mirrored into the shared registry so links,
 * selection, labels, and the camera read the same per-frame truth.
 *
 * Lives inside a <Body>, so its instances are in that body's local frame.
 */

import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { SAT_RADIUS, SAT_SEGMENTS, AREA_COLORS, getPlaneColor, UNKNOWN_TINT } from "../../config";
import { tokens } from "../../styles/tokens";
import { REGIME_TINT, type Regime } from "../../taxonomy/regime";
import { geoToWorld } from "../geo";
import { interpolatedSimTimeMs } from "../../sim/simClock";
import { isWorkerReady, readPosition, requestPropagate } from "../../sim/workerBridge";
import { propagateToSceneXYZ } from "../../sim/orbitalMath";
import {
  bodyMathFromFrame,
  kmPerRenderUnitFromEphemeris,
  type EphemerisNodeKeplerian,
  type SessionEphemeris,
} from "../../sim/ephemeris";
import type { ColorMode, NodeState, Selection } from "../../types";
import { FAMILY_TONE } from "../../explain/families";
import type { SatRelation } from "../../explain/gsCandidateRelations";
import { removeNode, setNodeLocalPosition } from "./positions";
import { useBodyFrame } from "./BodyFrame";
import type { HoverInfo } from "./Tooltip";

const MAX_SATELLITES = 10_000;

const _tmpMatrix = new THREE.Matrix4();
const _tmpColor = new THREE.Color();
const _workerPos = { x: 0, y: 0, z: 0 };

function satColor(node: NodeState, mode: ColorMode, regime: Regime | undefined): number {
  if (mode === "regime") return REGIME_TINT[regime ?? "unknown"].hex;
  if (mode === "area" && node.routing_area) return AREA_COLORS[node.routing_area] ?? UNKNOWN_TINT;
  if (mode === "plane" && node.plane != null) return getPlaneColor(node.plane);
  return tokens.colorNodeSatellite;
}

interface ConstellationProps {
  nodes: NodeState[];
  ephemeris: SessionEphemeris | null;
  colorMode: ColorMode;
  onSelect: (sel: Selection | null) => void;
  onFocusNode: (nodeId: string) => void;
  /** ctrl/cmd-click toggles an orbit pin for the satellite instead of selecting it. */
  onTogglePin: (id: string) => void;
  /** Hover a satellite -> tooltip; null clears it. */
  onHover: (info: HoverInfo | null) => void;
  /** On-select bloom: per-sat relation to the selected GS (family tone). null = no GS selected. */
  relations: Map<string, SatRelation> | null;
  /** Authored-orbit regime per node id (taxonomy/regime.ts), derived in App. */
  regimeById: ReadonlyMap<string, Regime>;
}

export function Constellation({
  nodes,
  ephemeris,
  colorMode,
  onSelect,
  onFocusNode,
  onTogglePin,
  onHover,
  relations,
  regimeById,
}: ConstellationProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const satIndex = useRef(new Map<string, number>());
  const indexToId = useRef<string[]>([]);
  const countRef = useRef(0);
  // Slots freed by vanished satellites, reused before growing countRef.
  // Without reuse a long-lived tab leaks one slot per satellite per session
  // switch and eventually overruns the MAX_SATELLITES instance buffer.
  const freeSlots = useRef<number[]>([]);
  const lastPropagateRef = useRef(0);
  // The body these satellites live in — written with each position so the registry resolves them
  // through this body's frame (no Earth assumption). Read via a ref so the useFrame closure is stable.
  const bodyFrame = useBodyFrame();
  const bodyId = bodyFrame.id;
  const bodyIdRef = useRef(bodyId);
  bodyIdRef.current = bodyId;

  const geometry = useMemo(
    () => new THREE.SphereGeometry(SAT_RADIUS, SAT_SEGMENTS, SAT_SEGMENTS),
    [],
  );
  const material = useMemo(() => new THREE.MeshBasicMaterial(), []);

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  // Reconcile instance slots + colors on snapshot / colorMode change. useLayoutEffect so
  // count is correct before the first paint (the args max-count would otherwise render
  // MAX_SATELLITES garbage instances for one frame).
  useLayoutEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    const seen = new Set<string>();
    for (const node of nodes) {
      if (node.node_type !== "satellite") continue;
      seen.add(node.node_id);
      let idx = satIndex.current.get(node.node_id);
      if (idx === undefined) {
        idx = freeSlots.current.pop() ?? countRef.current++;
        satIndex.current.set(node.node_id, idx);
        indexToId.current[idx] = node.node_id;
        const p = geoToWorld(
          node.lat_deg,
          node.lon_deg,
          node.alt_km,
          bodyFrame.radiusRender,
          bodyFrame.kmPerRenderUnit,
        );
        _tmpMatrix.makeTranslation(p.x, p.y, p.z);
        mesh.setMatrixAt(idx, _tmpMatrix);
        setNodeLocalPosition(node.node_id, bodyId, p.x, p.y, p.z);
      }
      // When a GS is selected, the scene blooms for it: candidate sats take their relation's
      // family tone, far/irrelevant sats dim so the eye goes to the candidates (spec on-select).
      // Otherwise the normal colorMode.
      if (relations) {
        const r = relations.get(node.node_id);
        if (r) _tmpColor.setHex(FAMILY_TONE[r.family].hex);
        else _tmpColor.setHex(satColor(node, colorMode, regimeById.get(node.node_id))).multiplyScalar(0.28);
      } else {
        _tmpColor.setHex(satColor(node, colorMode, regimeById.get(node.node_id)));
      }
      mesh.setColorAt(idx, _tmpColor);
    }
    // Satellites that vanished from the snapshot: collapse to a degenerate instance and
    // drop from the registry so a stale position never lingers.
    for (const [id, idx] of satIndex.current) {
      if (!seen.has(id)) {
        _tmpMatrix.makeScale(0, 0, 0);
        mesh.setMatrixAt(idx, _tmpMatrix);
        removeNode(id);
        satIndex.current.delete(id);
        delete indexToId.current[idx];
        freeSlots.current.push(idx);
      }
    }
    mesh.count = countRef.current;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    // Three caches InstancedMesh.boundingSphere on first raycast and never
    // invalidates it for matrix writes. A session switch can move satellites
    // outside the cached sphere, after which every click ray misses the
    // sphere and the raycast skips all instances — sat clicks go dead until
    // a reload. Null it so the next raycast recomputes from live matrices.
    mesh.boundingSphere = null;
  }, [nodes, colorMode, relations, regimeById, bodyId, bodyFrame.radiusRender, bodyFrame.kmPerRenderUnit]);

  // Per-frame propagation from the latest ephemeris and sim clock.
  useFrame(() => {
    const mesh = meshRef.current;
    if (!mesh || !ephemeris) return;
    const now = performance.now();
    const simMs = interpolatedSimTimeMs(now);
    if (simMs === null) return;
    const simTimeUnix = simMs / 1000;
    const epochUnix = ephemeris.epoch_unix;
    const kmPerRenderUnit = kmPerRenderUnitFromEphemeris(ephemeris);
    const workerReady = isWorkerReady();
    if (workerReady && now - lastPropagateRef.current > 2000) {
      requestPropagate(simTimeUnix, 1.0);
      lastPropagateRef.current = now;
    }
    for (const [nodeId, idx] of satIndex.current) {
      const ephNode = ephemeris.nodes[nodeId];
      if (!ephNode || ephNode.type !== "keplerian") continue;
      const keplerianNode: EphemerisNodeKeplerian = ephNode;
      const nodeBody = keplerianNode.reference_body;
      let x: number, y: number, z: number;
      if (workerReady && nodeBody === "earth" && readPosition(nodeId, simTimeUnix, _workerPos)) {
        x = _workerPos.x;
        y = _workerPos.y;
        z = _workerPos.z;
      } else {
        const frame = ephemeris.body_frames[nodeBody];
        if (!frame) {
          throw new Error(`SessionEphemeris missing body frame for node ${nodeId}: ${nodeBody}`);
        }
        [x, y, z] = propagateToSceneXYZ(
          {
            ...keplerianNode,
            body: bodyMathFromFrame(frame, kmPerRenderUnit),
          },
          epochUnix,
          simTimeUnix,
        );
      }
      _tmpMatrix.makeTranslation(x, y, z);
      mesh.setMatrixAt(idx, _tmpMatrix);
      setNodeLocalPosition(nodeId, bodyIdRef.current, x, y, z);
    }
    mesh.instanceMatrix.needsUpdate = true;
    // Same staleness as the reconcile above: positions move every frame, so
    // the raycast sphere must be recomputed lazily from current matrices.
    mesh.boundingSphere = null;
  }, -1); // after FrameDriver (-2) sets the frame rotation, before world-position consumers

  const byId = useMemo(() => new Map(nodes.map((n) => [n.node_id, n])), [nodes]);

  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    if (e.instanceId === undefined) return;
    const id = indexToId.current[e.instanceId];
    if (!id) return;
    e.stopPropagation();
    // ctrl/cmd-click toggles an orbit pin (legacy gpuPicker behavior); plain click selects.
    if (e.nativeEvent.ctrlKey || e.nativeEvent.metaKey) onTogglePin(id);
    else onSelect({ type: "satellite", id });
  };

  const handleDoubleClick = (e: ThreeEvent<MouseEvent>) => {
    if (e.instanceId === undefined) return;
    const id = indexToId.current[e.instanceId];
    if (!id) return;
    e.stopPropagation();
    onSelect({ type: "satellite", id });
    onFocusNode(id);
  };

  const handlePointerMove = (e: ThreeEvent<PointerEvent>) => {
    if (e.instanceId === undefined) return;
    const id = indexToId.current[e.instanceId];
    const node = id ? byId.get(id) : undefined;
    if (!node) return;
    // While a GS is selected, a candidate sat's tooltip carries its relation (the rejected-near
    // "FoR bound by 3°" the spec calls for) — same taxonomy words as the card.
    const r = id ? relations?.get(id) : undefined;
    const caption = r
      ? `${FAMILY_TONE[r.family].label}${r.reason ? `. ${r.reason}` : ""}`
      : undefined;
    onHover({ node, x: e.nativeEvent.clientX, y: e.nativeEvent.clientY, caption });
  };

  return (
    <instancedMesh
      ref={meshRef}
      args={[geometry, material, MAX_SATELLITES]}
      name="satellites"
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onPointerMove={handlePointerMove}
      onPointerOut={() => onHover(null)}
    />
  );
}
