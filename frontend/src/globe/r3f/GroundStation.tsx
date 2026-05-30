// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Ground stations — a billboarded antenna-glyph sprite per GS plus an elevation-coverage
 * cone (ring disc + outline) shown only while the GS is selected. Reproduces
 * globe/groundStations.ts: glyph canvas, GS_COLOR, GS_SIZE, the cone radius
 * (computeConeRadius, reused), the surface offset (R*1.001), and the orientation quaternion
 * (setFromUnitVectors((0,0,-1), outward)) verbatim. GS are static in the Earth local frame;
 * each registers its local position so the selection ring / links / camera can find it.
 *
 * Rendered inside <Body id="earth">. GS count is small (tens), so one sprite/cone per GS is
 * fine — instancing is unnecessary here (unlike the constellation).
 */

import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { type ThreeEvent } from "@react-three/fiber";
import { GS_COLOR, GS_SIZE } from "../../config";
import { geoToWorld } from "../geo";
import { computeConeRadius } from "../groundStations";
import { EARTH_RADIUS_RENDER } from "./units";
import { removeNode, setNodeLocalPosition } from "./positions";
import type { NodeState, Selection } from "../../types";

const GS_HEX = `#${GS_COLOR.toString(16).padStart(6, "0")}`;

/** The shared 64x64 antenna glyph texture (globe/groundStations.ts getSharedTexture). */
function makeGsTexture(): THREE.CanvasTexture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  ctx.strokeStyle = GS_HEX;
  ctx.fillStyle = GS_HEX;
  ctx.lineWidth = 3;
  // Base circle.
  ctx.beginPath();
  ctx.arc(size / 2, size * 0.7, 8, 0, Math.PI * 2);
  ctx.fill();
  // Dish.
  ctx.beginPath();
  ctx.moveTo(size * 0.2, size * 0.35);
  ctx.quadraticCurveTo(size / 2, size * 0.1, size * 0.8, size * 0.35);
  ctx.stroke();
  // Stem.
  ctx.beginPath();
  ctx.moveTo(size / 2, size * 0.7);
  ctx.lineTo(size / 2, size * 0.3);
  ctx.stroke();
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

interface GroundStationProps {
  node: NodeState;
  selected: boolean;
  orbitalAltKm: number;
  texture: THREE.CanvasTexture;
  onSelect: (sel: Selection | null) => void;
}

function GroundStation({ node, selected, orbitalAltKm, texture, onSelect }: GroundStationProps) {
  // Static local position + cone placement/orientation, recomputed only when the GS moves.
  const { position, conePosition, coneQuaternion, coneRadius, ringPoints } = useMemo(() => {
    const p = geoToWorld(node.lat_deg, node.lon_deg, node.alt_km);
    const outward = p.clone().normalize();
    const surface = outward.clone().multiplyScalar(EARTH_RADIUS_RENDER * 1.001);
    const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), outward);
    const minElev = node.min_elevation_deg ?? 25;
    const radius = computeConeRadius(minElev, orbitalAltKm);
    const pts: number[] = [];
    for (let i = 0; i <= 48; i++) {
      const a = (i / 48) * Math.PI * 2;
      pts.push(Math.cos(a) * radius, Math.sin(a) * radius, 0);
    }
    return {
      position: [p.x, p.y, p.z] as [number, number, number],
      conePosition: [surface.x, surface.y, surface.z] as [number, number, number],
      coneQuaternion: quat,
      coneRadius: radius,
      ringPoints: new Float32Array(pts),
    };
  }, [node.lat_deg, node.lon_deg, node.alt_km, node.min_elevation_deg, orbitalAltKm]);

  const ringGeometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(ringPoints, 3));
    return g;
  }, [ringPoints]);

  // Register the GS local position so the selection ring / links / camera can resolve it.
  useEffect(() => {
    setNodeLocalPosition(node.node_id, position[0], position[1], position[2]);
    return () => removeNode(node.node_id);
  }, [node.node_id, position]);

  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect({ type: "ground_station", id: node.node_id });
  };

  return (
    <group>
      <sprite scale={[GS_SIZE, GS_SIZE, 1]} position={position} onClick={handleClick}>
        <spriteMaterial map={texture} sizeAttenuation />
      </sprite>
      {/* Elevation coverage cone — disc + outline, visible only while selected. */}
      <mesh position={conePosition} quaternion={coneQuaternion} visible={selected}>
        <ringGeometry args={[0, coneRadius, 48]} />
        <meshBasicMaterial
          color={GS_COLOR}
          transparent
          opacity={0.05}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>
      <lineLoop position={conePosition} quaternion={coneQuaternion} visible={selected} geometry={ringGeometry}>
        <lineBasicMaterial color={GS_COLOR} transparent opacity={0.2} depthWrite={false} />
      </lineLoop>
    </group>
  );
}

interface GroundStationsProps {
  nodes: NodeState[];
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
}

export function GroundStations({ nodes, selection, onSelect }: GroundStationsProps) {
  const texture = useMemo(() => makeGsTexture(), []);
  useEffect(() => () => texture.dispose(), [texture]);

  const gsNodes = nodes.filter((n) => n.node_type === "ground_station");
  // Cone footprint sized to the constellation's orbital altitude (first sat, fallback 550).
  const orbitalAltKm = nodes.find((n) => n.node_type === "satellite")?.alt_km ?? 550;

  return (
    <>
      {gsNodes.map((node) => (
        <GroundStation
          key={node.node_id}
          node={node}
          selected={selection?.type === "ground_station" && selection.id === node.node_id}
          orbitalAltKm={orbitalAltKm}
          texture={texture}
          onSelect={onSelect}
        />
      ))}
    </>
  );
}
