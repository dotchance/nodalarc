/** Link rendering using Line2 for pixel-width lines.
 *  Links are drawn as great-circle arcs raised above the earth surface.
 *  Fail-flash: red hold 5s → fade to dark → hidden.
 *  Link up: immediate appear + brightness pulse.
 */

import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import {
  LINK_ISL_COLOR,
  LINK_GROUND_COLOR,
  LINK_FAIL_COLOR,
  LINK_INACTIVE_COLOR,
  LINK_ISL_WIDTH,
  LINK_GROUND_WIDTH,
  FAIL_HOLD_MS,
  FAIL_FADE_MS,
} from "../config";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";
import type { LinkState } from "../types";

interface LinkEntry {
  line: Line2;
  geometry: LineGeometry;
  material: LineMaterial;
  state: string;
  nodeA: string;
  nodeB: string;
  isGround: boolean;
  /** Timestamp when link went down (for fail-flash). */
  failTime: number | null;
  /** Timestamp when link came up (for brightness pulse). */
  upTime: number | null;
  baseColor: THREE.Color;
}

const links = new Map<string, LinkEntry>();
let resolution = new THREE.Vector2(window.innerWidth, window.innerHeight);

window.addEventListener("resize", () => {
  resolution.set(window.innerWidth, window.innerHeight);
  for (const entry of links.values()) {
    entry.material.resolution.copy(resolution);
  }
});

function linkKey(a: string, b: string): string {
  return a < b ? `${a}:${b}` : `${b}:${a}`;
}

function isGroundLink(nodeA: string, nodeB: string): boolean {
  return nodeA.startsWith("gs-") || nodeB.startsWith("gs-");
}

const _va = new THREE.Vector3();
const _vb = new THREE.Vector3();
const _pt = new THREE.Vector3();

/**
 * Build smooth great-circle arc between two 3D positions.
 * Slerps the direction on the unit sphere and interpolates altitude,
 * producing a curve that follows the earth's curvature at orbital height.
 */
function arcPositions(a: THREE.Vector3, b: THREE.Vector3): number[] {
  _va.copy(a).normalize();
  _vb.copy(b).normalize();
  const dot = Math.min(1, Math.max(-1, _va.dot(_vb)));
  const angle = Math.acos(dot);

  // Enough segments for a visually smooth curve
  const segments = Math.max(24, Math.ceil(angle * 40));
  const altA = a.length();
  const altB = b.length();

  if (angle < 0.001) {
    return [a.x, a.y, a.z, b.x, b.y, b.z];
  }

  const sinAngle = Math.sin(angle);
  const positions: number[] = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const wA = Math.sin((1 - t) * angle) / sinAngle;
    const wB = Math.sin(t * angle) / sinAngle;
    _pt.set(
      _va.x * wA + _vb.x * wB,
      _va.y * wA + _vb.y * wB,
      _va.z * wA + _vb.z * wB,
    );
    const r = altA + (altB - altA) * t;
    _pt.normalize().multiplyScalar(r);
    positions.push(_pt.x, _pt.y, _pt.z);
  }
  return positions;
}

export function updateLinks(
  linkStates: LinkState[],
  scene: THREE.Scene,
  _showAllLinks: boolean,
): void {
  const now = performance.now();
  const active = new Set<string>();

  for (const ls of linkStates) {
    const key = linkKey(ls.node_a, ls.node_b);
    active.add(key);
    const ground = isGroundLink(ls.node_a, ls.node_b);

    const existing = links.get(key);
    if (existing) {
      // Link was down, now back up
      if (existing.state !== "active" && ls.state === "active") {
        existing.upTime = now;
        existing.failTime = null;
        existing.line.visible = true;
      }
      existing.state = ls.state;
    } else {
      // New link
      const geometry = new LineGeometry();
      geometry.setPositions([0, 0, 0, 0, 0, 0]); // placeholder
      const color = ground ? LINK_GROUND_COLOR : LINK_ISL_COLOR;
      const width = ground ? LINK_GROUND_WIDTH : LINK_ISL_WIDTH;
      const material = new LineMaterial({
        color,
        linewidth: width,
        resolution,
        dashed: ground,
        dashScale: ground ? 4 : 1,
        dashSize: ground ? 0.5 : 1,
        gapSize: ground ? 0.3 : 0,
      });

      const line = new Line2(geometry, material);
      line.computeLineDistances();
      scene.add(line);

      links.set(key, {
        line,
        geometry,
        material,
        state: ls.state,
        nodeA: ls.node_a,
        nodeB: ls.node_b,
        isGround: ground,
        failTime: null,
        upTime: now,
        baseColor: new THREE.Color(color),
      });
    }
  }

  // Mark removed links as failed (fail-flash)
  for (const [key, entry] of links) {
    if (!active.has(key) && entry.state === "active") {
      entry.state = "inactive";
      entry.failTime = now;
      entry.upTime = null;
    }
  }
}

export function animateLinks(): void {
  const now = performance.now();
  const sats = getSatellites();
  const gss = getGroundStations();

  for (const [key, entry] of links) {
    // Get endpoint positions
    const posA = sats.get(entry.nodeA)?.mesh.position ?? gss.get(entry.nodeA)?.sprite.position;
    const posB = sats.get(entry.nodeB)?.mesh.position ?? gss.get(entry.nodeB)?.sprite.position;

    if (!posA || !posB) {
      entry.line.visible = false;
      continue;
    }

    // Update geometry as a great-circle arc
    const arcPts = arcPositions(posA, posB);
    entry.geometry.setPositions(arcPts);
    entry.line.computeLineDistances();

    // Fail-flash animation
    if (entry.failTime !== null) {
      const elapsed = now - entry.failTime;
      if (elapsed < FAIL_HOLD_MS) {
        // Hold red
        entry.material.color.setHex(LINK_FAIL_COLOR);
        entry.line.visible = true;
      } else if (elapsed < FAIL_HOLD_MS + FAIL_FADE_MS) {
        // Fade to dark
        const t = (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS;
        const failColor = new THREE.Color(LINK_FAIL_COLOR);
        const darkColor = new THREE.Color(LINK_INACTIVE_COLOR);
        entry.material.color.copy(failColor).lerp(darkColor, t);
        entry.line.visible = true;
      } else {
        // Hidden
        entry.line.visible = false;
        // Clean up old failed links
        links.delete(key);
        continue;
      }
    } else if (entry.upTime !== null) {
      // Brightness pulse on link up (0.5s)
      const elapsed = now - entry.upTime;
      if (elapsed < 500) {
        const t = elapsed / 500;
        const bright = new THREE.Color(0xffffff);
        entry.material.color.copy(bright).lerp(entry.baseColor, t);
      } else {
        entry.material.color.copy(entry.baseColor);
        entry.upTime = null;
      }
      entry.line.visible = true;
    } else {
      entry.material.color.copy(entry.baseColor);
      entry.line.visible = true;
    }
  }
}

export function getLinks(): Map<string, LinkEntry> {
  return links;
}
