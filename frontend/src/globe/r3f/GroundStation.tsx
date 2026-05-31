// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Ground stations — a billboarded antenna-glyph sprite per GS plus the elevation-coverage
 * cone shown while the GS is selected. The cone uses the EFFECTIVE envelope floor (the
 * binding min-elevation the composer computes — e.g. a 30° FoR-derived floor that dominates
 * a configured 25° mask), NOT the raw configured mask, so the overlay agrees with the
 * decision card and does not make a FoR-bound satellite look eligible (the Denver
 * confusion). The configured mask is drawn as a faint wider reference when it differs.
 *
 * Glyph/cone geometry (GS_COLOR, GS_SIZE, R*1.001 surface offset, (0,0,-1)->outward
 * orientation, computeConeRadius) reproduce globe/groundStations.ts. GS are static in the
 * Earth local frame; each registers its local position so the selection ring / links /
 * camera can find it. Rendered inside <Body id="earth">; one sprite/cone per GS (few GS).
 */

import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { GS_COLOR, GS_SIZE } from "../../config";
import { geoToWorld } from "../geo";
import { computeConeRadius } from "../groundStations";
import { FAMILY_TONE } from "../../explain/families";
import { groundStationFamily, type GsFamily } from "../../explain/groundStationFamily";
import type { EffectiveEnvelopeFacts } from "../../explain/types";
import { EARTH_RADIUS_RENDER } from "./units";
import { removeNode, setNodeLocalPosition } from "./positions";
import { useBodyFrame } from "./BodyFrame";
import type { HoverInfo } from "./Tooltip";
import type { ActuationNotice, LinkState, NodeState, Selection } from "../../types";

/** The shared 64x64 antenna glyph texture. Drawn WHITE so the per-GS spriteMaterial.color tints
 *  it to the canonical family tone (globe/groundStations.ts getSharedTexture, recolored). */
function makeGsTexture(): THREE.CanvasTexture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  ctx.strokeStyle = "#ffffff";
  ctx.fillStyle = "#ffffff";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(size / 2, size * 0.7, 8, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(size * 0.2, size * 0.35);
  ctx.quadraticCurveTo(size / 2, size * 0.1, size * 0.8, size * 0.35);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(size / 2, size * 0.7);
  ctx.lineTo(size / 2, size * 0.3);
  ctx.stroke();
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

function ringGeometry(radius: number): THREE.BufferGeometry {
  const pts: number[] = [];
  for (let i = 0; i <= 48; i++) {
    const a = (i / 48) * Math.PI * 2;
    pts.push(Math.cos(a) * radius, Math.sin(a) * radius, 0);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pts), 3));
  return g;
}

interface GroundStationProps {
  node: NodeState;
  selected: boolean;
  orbitalAltKm: number;
  texture: THREE.CanvasTexture;
  /** Effective binding floor (deg) for the selected GS, from the decision explanation. */
  effectiveMinElevDeg: number | null;
  /** Configured mask (deg) for the selected GS, from the decision explanation. */
  configuredMinElevDeg: number | null;
  /** Default-state canonical family (snapshot-derived) — drives glyph tone + fault pulse. */
  family: GsFamily;
  onSelect: (sel: Selection | null) => void;
  onHover: (info: HoverInfo | null) => void;
}

function GroundStation({
  node,
  selected,
  orbitalAltKm,
  texture,
  effectiveMinElevDeg,
  configuredMinElevDeg,
  family,
  onSelect,
  onHover,
}: GroundStationProps) {
  const spriteRef = useRef<THREE.Sprite>(null);
  const bodyId = useBodyFrame().id;
  const faulted = family.family === "faulted";
  // Pulse the glyph only for a true fault (spec: "Fault pulse only for real faults"). Driven by
  // the R3F render clock (wall-clock), so it keeps alerting even when the sim is paused.
  useFrame((state) => {
    const s = spriteRef.current;
    if (!s) return;
    const scale = faulted ? GS_SIZE * (1 + 0.25 * Math.sin(state.clock.elapsedTime * 6)) : GS_SIZE;
    s.scale.set(scale, scale, 1);
  });

  // Taxonomy caption for the hover tooltip — same family label the inspector/logs use.
  const caption = `${FAMILY_TONE[family.family].label}${family.reason ? `. ${family.reason}` : ""}`;
  const geom = useMemo(() => {
    const p = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
    const outward = p.clone().normalize();
    const surface = outward.clone().multiplyScalar(EARTH_RADIUS_RENDER * 1.001);
    const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), outward);
    // Only the selected GS has resolved envelope floors; others (cone hidden) fall back to
    // the configured mask. Effective is the binding floor; configured is the wider mask.
    const fallback = node.min_elevation_deg ?? 25;
    const effElev = (selected ? effectiveMinElevDeg : null) ?? fallback;
    const confElev = (selected ? configuredMinElevDeg : null) ?? fallback;
    const effRadius = computeConeRadius(effElev, orbitalAltKm);
    const confRadius = computeConeRadius(confElev, orbitalAltKm);
    // Show the configured reference only when the mask is genuinely non-binding (the
    // effective floor is higher → a tighter cone), i.e. the FoR-bound / dead-knob case.
    const showConfigured = confRadius - effRadius > 0.5;
    return {
      position: [p.x, p.y, p.z] as [number, number, number],
      conePosition: [surface.x, surface.y, surface.z] as [number, number, number],
      coneQuaternion: quat,
      effRadius,
      confRadius,
      effRing: ringGeometry(effRadius),
      confRing: showConfigured ? ringGeometry(confRadius) : null,
      showConfigured,
    };
  }, [
    node.lat_deg,
    node.lon_deg,
    node.alt_km,
    node.min_elevation_deg,
    orbitalAltKm,
    selected,
    effectiveMinElevDeg,
    configuredMinElevDeg,
  ]);

  useEffect(() => {
    setNodeLocalPosition(node.node_id, bodyId, geom.position[0], geom.position[1], geom.position[2]);
    return () => removeNode(node.node_id);
  }, [node.node_id, bodyId, geom.position]);

  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    // A modified click is reserved for satellite orbit-pinning; swallow it on a GS (the legacy
    // gpuPicker returns on a modified click for anything that is not a satellite).
    if (e.nativeEvent.ctrlKey || e.nativeEvent.metaKey) return;
    onSelect({ type: "ground_station", id: node.node_id });
  };

  return (
    <group>
      <sprite
        ref={spriteRef}
        scale={[GS_SIZE, GS_SIZE, 1]}
        position={geom.position}
        onClick={handleClick}
        onPointerMove={(e) =>
          onHover({ node, x: e.nativeEvent.clientX, y: e.nativeEvent.clientY, caption })
        }
        onPointerOut={() => onHover(null)}
      >
        <spriteMaterial map={texture} color={FAMILY_TONE[family.family].hex} sizeAttenuation />
      </sprite>
      {/* Effective coverage cone (the binding floor) — filled disc + strong outline. */}
      <mesh position={geom.conePosition} quaternion={geom.coneQuaternion} visible={selected}>
        <ringGeometry args={[0, geom.effRadius, 48]} />
        <meshBasicMaterial
          color={GS_COLOR}
          transparent
          opacity={0.06}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>
      <lineLoop
        position={geom.conePosition}
        quaternion={geom.coneQuaternion}
        visible={selected}
        geometry={geom.effRing}
      >
        <lineBasicMaterial color={GS_COLOR} transparent opacity={0.3} depthWrite={false} />
      </lineLoop>
      {/* Configured-mask reference (wider, faint) — only when the mask is non-binding. */}
      {geom.confRing && (
        <lineLoop
          position={geom.conePosition}
          quaternion={geom.coneQuaternion}
          visible={selected}
          geometry={geom.confRing}
        >
          <lineBasicMaterial color={GS_COLOR} transparent opacity={0.1} depthWrite={false} />
        </lineLoop>
      )}
    </group>
  );
}

interface GroundStationsProps {
  nodes: NodeState[];
  selection: Selection | null;
  /** Active links + Scheduler actuation notices from the snapshot — drive default-state family. */
  links: LinkState[];
  actuationNotices: ActuationNotice[];
  /** The selected GS's effective envelope (binding floor + configured mask), lifted from Scene
   *  so the cone, the lead-line, and the sat tinting share ONE decision-explanation fetch. */
  envelope: EffectiveEnvelopeFacts | null;
  onSelect: (sel: Selection | null) => void;
  onHover: (info: HoverInfo | null) => void;
}

export function GroundStations({
  nodes,
  selection,
  links,
  actuationNotices,
  envelope,
  onSelect,
  onHover,
}: GroundStationsProps) {
  const texture = useMemo(() => makeGsTexture(), []);
  useEffect(() => () => texture.dispose(), [texture]);

  const gsNodes = nodes.filter((n) => n.node_type === "ground_station");
  const orbitalAltKm = nodes.find((n) => n.node_type === "satellite")?.alt_km ?? 550;

  const selectedGsId = selection?.type === "ground_station" ? selection.id : null;

  return (
    <>
      {gsNodes.map((node) => {
        const isSelected = selectedGsId === node.node_id;
        return (
          <GroundStation
            key={node.node_id}
            node={node}
            selected={isSelected}
            orbitalAltKm={orbitalAltKm}
            texture={texture}
            effectiveMinElevDeg={isSelected ? (envelope?.effective_min_elevation_deg ?? null) : null}
            configuredMinElevDeg={isSelected ? (envelope?.configured_min_elevation_deg ?? null) : null}
            family={groundStationFamily(node.node_id, links, actuationNotices)}
            onSelect={onSelect}
            onHover={onHover}
          />
        );
      })}
    </>
  );
}
