// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * GroundTracks — faint sub-satellite ground traces on the Earth surface, OFF by default.
 * Faithful port of globe/groundTracks.ts: for each satellite with velocity, a +/-10-minute
 * track is extrapolated LINEARLY (40 steps, 30 s each) from the snapshot ground position
 * (geoToWorld) along the scene-unit velocity (velocityToScene), with every sample reprojected
 * onto the surface sphere at SURFACE_OFFSET = EARTH_RADIUS_RENDER*1.002 (avoids z-fighting).
 * Each track is one THREE.Line; color is getTrackColor — routing-area AREA_COLORS first, then
 * getPlaneColor, else the shared unknown tint — with a faint LineBasicMaterial
 * (opacity 0.15, depthWrite:false).
 *
 * The tracks are Earth-local curves (built from geoToWorld, which is the same body-local frame
 * <Constellation>/<GroundStation> write), so this mounts as a BODY CHILD inside <Body
 * id="earth"> — the frame rotation is carried by the parent group, exactly like the legacy
 * version which added each line to earthFrame.
 *
 * Data-driven, not per-frame: the legacy updateGroundTracks ran on snapshot/node updates, not
 * every animation frame (the linear extrapolation is a snapshot artifact, not a live curve).
 * We mirror that — tracks rebuild in an effect keyed on `nodes`, reconciling per-satellite Line
 * objects (create / in-place geometry swap / remove vanished). No useFrame: nothing here moves
 * between snapshots. All geometries/materials are disposed on unmount and on satellite removal.
 */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { AREA_COLORS, getPlaneColor, UNKNOWN_TINT } from "../../config";
import { geoToWorld, velocityToScene } from "../geo";
import { useBodyFrame } from "./BodyFrame";
import type { NodeState } from "../../types";

const STEPS = 40;
const DT_PER_STEP = 30; // seconds per step → +/-10 minutes total

function getTrackColor(node: NodeState): number {
  if (node.routing_area && AREA_COLORS[node.routing_area]) {
    return AREA_COLORS[node.routing_area]!;
  }
  if (node.plane != null) {
    return getPlaneColor(node.plane);
  }
  return UNKNOWN_TINT;
}

/** Build the +/-10-minute surface-projected track points for one satellite. */
function buildTrackPoints(
  sat: NodeState,
  radiusRender: number,
  kmPerRenderUnit: number,
): THREE.Vector3[] {
  const surfaceOffset = radiusRender * 1.002;
  const pos = geoToWorld(sat.lat_deg, sat.lon_deg, sat.alt_km, radiusRender, kmPerRenderUnit);
  const vel = velocityToScene(
    sat.vel_x_km_s ?? 0,
    sat.vel_y_km_s ?? 0,
    sat.vel_z_km_s ?? 0,
    kmPerRenderUnit,
  );
  const points: THREE.Vector3[] = [];
  for (let i = -STEPS / 2; i <= STEPS / 2; i++) {
    const t = i * DT_PER_STEP;
    const px = pos.x + vel.x * t;
    const py = pos.y + vel.y * t;
    const pz = pos.z + vel.z * t;
    const len = Math.sqrt(px * px + py * py + pz * pz);
    if (len < 0.01) continue;
    const scale = surfaceOffset / len;
    points.push(new THREE.Vector3(px * scale, py * scale, pz * scale));
  }
  return points;
}

interface GroundTracksProps {
  /** All session nodes; satellites with velocity get a ground track. */
  nodes: NodeState[];
  /** Master toggle — OFF by default; when false, all tracks are torn down. */
  enabled: boolean;
}

export function GroundTracks({ nodes, enabled }: GroundTracksProps) {
  const { radiusRender, kmPerRenderUnit } = useBodyFrame();
  const groupRef = useRef<THREE.Group>(null);
  const tracksRef = useRef(new Map<string, THREE.Line>());

  const clearAll = (): void => {
    const group = groupRef.current;
    const tracks = tracksRef.current;
    for (const [id, line] of tracks) {
      group?.remove(line);
      line.geometry.dispose();
      (line.material as THREE.Material).dispose();
      tracks.delete(id);
    }
  };

  // Dispose everything on unmount.
  useEffect(() => () => clearAll(), []);

  // Data-driven reconcile, mirroring updateGroundTracks: create / in-place geometry swap /
  // remove vanished satellites. Runs on node updates and on the enabled toggle.
  useEffect(() => {
    const group = groupRef.current;
    if (!group) return;
    if (!enabled) {
      clearAll();
      return;
    }
    const tracks = tracksRef.current;
    const seen = new Set<string>();

    for (const sat of nodes) {
      if (sat.node_type !== "satellite" || sat.vel_x_km_s == null) continue;
      seen.add(sat.node_id);

      const points = buildTrackPoints(sat, radiusRender, kmPerRenderUnit);
      if (points.length < 2) continue;

      const existing = tracks.get(sat.node_id);
      if (existing) {
        existing.geometry.dispose();
        existing.geometry = new THREE.BufferGeometry().setFromPoints(points);
      } else {
        const geometry = new THREE.BufferGeometry().setFromPoints(points);
        const material = new THREE.LineBasicMaterial({
          color: getTrackColor(sat),
          transparent: true,
          opacity: 0.15,
          depthWrite: false,
        });
        const line = new THREE.Line(geometry, material);
        group.add(line);
        tracks.set(sat.node_id, line);
      }
    }

    // Remove tracks for satellites that vanished from the snapshot.
    for (const [id, line] of tracks) {
      if (!seen.has(id)) {
        group.remove(line);
        line.geometry.dispose();
        (line.material as THREE.Material).dispose();
        tracks.delete(id);
      }
    }
  }, [nodes, enabled, radiusRender, kmPerRenderUnit]);

  return <group ref={groupRef} name="ground-tracks" />;
}
