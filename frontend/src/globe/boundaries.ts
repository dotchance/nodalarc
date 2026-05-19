// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
// Political boundaries — Natural Earth 110m country borders on the globe.
//
// Loads GeoJSON from /ne_110m_countries.geojson (bundled in the VF
// container for offline operation). Renders as ONE LineSegments object
// using disconnected segment pairs. One draw call for all borders.

import * as THREE from "three";
import { tokens } from "../styles/tokens";

const EARTH_RADIUS_SCENE = tokens.earthRadius;
const BOUNDARY_COLOR = 0x88aacc;
const BOUNDARY_OPACITY = 0.55;

let boundaryMesh: THREE.LineSegments | null = null;
let loaded = false;

function geoToXYZ(lon: number, lat: number, out: { x: number; y: number; z: number }): void {
  const latR = (lat * Math.PI) / 180;
  const lonR = (lon * Math.PI) / 180;
  const r = EARTH_RADIUS_SCENE * 1.001;
  out.x = r * Math.cos(latR) * Math.cos(lonR);
  out.y = r * Math.sin(latR);
  out.z = -r * Math.cos(latR) * Math.sin(lonR);
}

export async function loadBoundaries(earthFrame: THREE.Object3D): Promise<void> {
  if (loaded) return;
  loaded = true;

  try {
    const resp = await fetch("/ne_110m_countries.geojson");
    if (!resp.ok) return;
    const geojson = await resp.json();

    // Collect all line segments from all polygon rings.
    // Each ring edge becomes a segment pair: (v0, v1), (v1, v2), ...
    const segments: number[] = [];
    const pt = { x: 0, y: 0, z: 0 };
    const prevPt = { x: 0, y: 0, z: 0 };

    for (const feature of geojson.features) {
      const geom = feature.geometry;
      if (!geom) continue;

      let rings: number[][][] = [];
      if (geom.type === "Polygon") {
        rings = geom.coordinates;
      } else if (geom.type === "MultiPolygon") {
        for (const polygon of geom.coordinates) {
          rings.push(...polygon);
        }
      }

      for (const ring of rings) {
        for (let i = 0; i < ring.length; i++) {
          const coord = ring[i];
          if (!coord || coord[0] === undefined || coord[1] === undefined) continue;
          geoToXYZ(coord[0], coord[1], pt);

          if (i > 0) {
            segments.push(prevPt.x, prevPt.y, prevPt.z, pt.x, pt.y, pt.z);
          }
          prevPt.x = pt.x;
          prevPt.y = pt.y;
          prevPt.z = pt.z;
        }
      }
    }

    if (segments.length === 0) return;

    const posArray = new Float32Array(segments);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(posArray, 3));

    const material = new THREE.LineBasicMaterial({
      color: BOUNDARY_COLOR,
      transparent: true,
      opacity: BOUNDARY_OPACITY,
      depthWrite: false,
    });

    boundaryMesh = new THREE.LineSegments(geometry, material);
    boundaryMesh.renderOrder = 2;
    earthFrame.add(boundaryMesh);
  } catch {
    // GeoJSON not available — boundaries silently absent
  }
}

export function setBoundariesVisible(visible: boolean): void {
  if (boundaryMesh) boundaryMesh.visible = visible;
}

export function clearBoundaries(earthFrame: THREE.Object3D): void {
  if (boundaryMesh) {
    earthFrame.remove(boundaryMesh);
    boundaryMesh.geometry.dispose();
    (boundaryMesh.material as THREE.Material).dispose();
    boundaryMesh = null;
    loaded = false;
  }
}
