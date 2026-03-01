/** Link rendering using Line2 for pixel-width lines.
 *  Per VF spec:
 *   - Intra-area ISL: solid, area green, 1px
 *   - Cross-area ISL: dashed, white, 50% opacity, 1.5px
 *   - Ground: solid, teal, 1.5px
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
  LINK_CROSS_AREA_COLOR,
  LINK_CROSS_AREA_OPACITY,
  LINK_FAIL_COLOR,
  LINK_INACTIVE_COLOR,
  LINK_ISL_WIDTH,
  LINK_CROSS_AREA_WIDTH,
  LINK_GROUND_WIDTH,
  FAIL_HOLD_MS,
  FAIL_FADE_MS,
} from "../config";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";
import type { LinkState } from "../types";

const _mid = new THREE.Vector3();
const _outward = new THREE.Vector3();

/**
 * Build a gently bowed line between two positions.
 * The midpoint is pushed outward from earth center by a fraction of
 * the chord length, giving links a smooth curved appearance.
 */
function bowedPositions(a: THREE.Vector3, b: THREE.Vector3): number[] {
  const segments = 16;
  const positions: number[] = [];

  // Outward direction at midpoint (away from earth center)
  _mid.lerpVectors(a, b, 0.5);
  _outward.copy(_mid).normalize();

  // Bow amount: 3% of chord length, pushed outward
  const chord = a.distanceTo(b);
  const lift = chord * 0.03;

  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const x = a.x + (b.x - a.x) * t;
    const y = a.y + (b.y - a.y) * t;
    const z = a.z + (b.z - a.z) * t;
    const bow = 4 * t * (1 - t) * lift;
    positions.push(
      x + _outward.x * bow,
      y + _outward.y * bow,
      z + _outward.z * bow,
    );
  }
  return positions;
}

interface LinkEntry {
  line: Line2;
  geometry: LineGeometry;
  material: LineMaterial;
  state: string;
  nodeA: string;
  nodeB: string;
  isGround: boolean;
  isCrossArea: boolean;
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

/** Check if a non-ground link crosses routing area boundaries. */
function isCrossAreaLink(nodeA: string, nodeB: string): boolean {
  const sats = getSatellites();
  const areaA = sats.get(nodeA)?.nodeState.routing_area;
  const areaB = sats.get(nodeB)?.nodeState.routing_area;
  if (areaA == null || areaB == null) return false;
  return areaA !== areaB;
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
    const crossArea = !ground && isCrossAreaLink(ls.node_a, ls.node_b);

    const existing = links.get(key);
    if (existing) {
      // Link was down, now back up
      if (existing.state !== "active" && ls.state === "active") {
        existing.upTime = now;
        existing.failTime = null;
        existing.line.visible = true;
      }
      existing.state = ls.state;
      // Update cross-area status (areas can change over time)
      if (existing.isCrossArea !== crossArea && !ground) {
        existing.isCrossArea = crossArea;
        if (crossArea) {
          existing.material.color.setHex(LINK_CROSS_AREA_COLOR);
          existing.material.opacity = LINK_CROSS_AREA_OPACITY;
          existing.material.linewidth = LINK_CROSS_AREA_WIDTH;
          existing.material.dashed = true;
          existing.material.dashScale = 4;
          existing.material.dashSize = 0.5;
          existing.material.gapSize = 0.3;
          existing.baseColor.setHex(LINK_CROSS_AREA_COLOR);
        } else {
          existing.material.color.setHex(LINK_ISL_COLOR);
          existing.material.opacity = 0.35;
          existing.material.linewidth = LINK_ISL_WIDTH;
          existing.material.dashed = false;
          existing.baseColor.setHex(LINK_ISL_COLOR);
        }
        existing.material.needsUpdate = true;
      }
    } else {
      // New link
      const geometry = new LineGeometry();
      geometry.setPositions([0, 0, 0, 0, 0, 0]); // placeholder

      let color: number;
      let width: number;
      let opacity: number;
      let dashed = false;

      if (ground) {
        color = LINK_GROUND_COLOR;
        width = LINK_GROUND_WIDTH;
        opacity = 0.6;
      } else if (crossArea) {
        color = LINK_CROSS_AREA_COLOR;
        width = LINK_CROSS_AREA_WIDTH;
        opacity = LINK_CROSS_AREA_OPACITY;
        dashed = true;
      } else {
        color = LINK_ISL_COLOR;
        width = LINK_ISL_WIDTH;
        opacity = 0.35;
      }

      const material = new LineMaterial({
        color,
        linewidth: width,
        resolution,
        transparent: true,
        opacity,
        dashed,
        dashScale: dashed ? 4 : 1,
        dashSize: dashed ? 0.5 : 1,
        gapSize: dashed ? 0.3 : 0,
        depthWrite: false,
      });

      const line = new Line2(geometry, material);
      if (dashed) line.computeLineDistances();
      scene.add(line);

      links.set(key, {
        line,
        geometry,
        material,
        state: ls.state,
        nodeA: ls.node_a,
        nodeB: ls.node_b,
        isGround: ground,
        isCrossArea: crossArea,
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

    // Gently bowed curve so links read as smooth, not polygonal
    entry.geometry.setPositions(bowedPositions(posA, posB));
    if (entry.isCrossArea) entry.line.computeLineDistances();

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
