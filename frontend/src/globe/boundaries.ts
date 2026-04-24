// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
// Political boundaries — Natural Earth 110m country borders on the globe.
//
// Loads GeoJSON from /ne_110m_countries.geojson (bundled in the VF
// container for offline operation). Renders as line geometry on the
// earth sphere using design token colors.

import * as THREE from "three";
import { tokens } from "../styles/tokens";

const EARTH_RADIUS_SCENE = tokens.earthRadius;
const BOUNDARY_COLOR = 0x88aacc;
const BOUNDARY_OPACITY = 0.55;

let boundaryGroup: THREE.Group | null = null;
let loaded = false;

function geoToVec3(lon: number, lat: number): THREE.Vector3 {
  const latR = (lat * Math.PI) / 180;
  const lonR = (lon * Math.PI) / 180;
  const r = EARTH_RADIUS_SCENE * 1.001;
  return new THREE.Vector3(
    r * Math.cos(latR) * Math.cos(lonR),
    r * Math.sin(latR),
    -r * Math.cos(latR) * Math.sin(lonR),
  );
}

function buildLineGeometry(coords: number[][]): THREE.BufferGeometry | null {
  if (coords.length < 2) return null;
  const points: THREE.Vector3[] = [];
  for (const [lon, lat] of coords) {
    if (lon !== undefined && lat !== undefined) {
      points.push(geoToVec3(lon, lat));
    }
  }
  if (points.length < 2) return null;
  return new THREE.BufferGeometry().setFromPoints(points);
}

export async function loadBoundaries(earthFrame: THREE.Object3D): Promise<void> {
  if (loaded) return;
  loaded = true;

  try {
    const resp = await fetch("/ne_110m_countries.geojson");
    if (!resp.ok) return;
    const geojson = await resp.json();

    boundaryGroup = new THREE.Group();
    boundaryGroup.name = "boundaries";

    const material = new THREE.LineBasicMaterial({
      color: BOUNDARY_COLOR,
      transparent: true,
      opacity: BOUNDARY_OPACITY,
      depthWrite: false,
    });

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
        const lineGeo = buildLineGeometry(ring);
        if (lineGeo) {
          const line = new THREE.Line(lineGeo, material);
          line.renderOrder = 2;
          boundaryGroup.add(line);
        }
      }
    }

    earthFrame.add(boundaryGroup);
  } catch {
    // GeoJSON not available — boundaries silently absent
  }
}

export function setBoundariesVisible(visible: boolean): void {
  if (boundaryGroup) boundaryGroup.visible = visible;
}

export function clearBoundaries(earthFrame: THREE.Object3D): void {
  if (boundaryGroup) {
    earthFrame.remove(boundaryGroup);
    for (const child of boundaryGroup.children) {
      if (child instanceof THREE.Line) {
        child.geometry.dispose();
      }
    }
    boundaryGroup = null;
    loaded = false;
  }
}
