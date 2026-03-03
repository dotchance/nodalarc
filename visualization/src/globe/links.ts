/** Link rendering using Line2 for pixel-width lines.
 *  Per VF spec Sections 7.3, 7.4, 10.2:
 *   - ISL (all): solid, muted green #44cc66, 1.5px
 *   - Ground: dashed (16-unit dash, 8-unit gap), cyan #00ccff, 2px
 *  Fail-flash: red hold 5s -> fade to dark -> hidden.
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
  /** Timestamp when link went down (for fail-flash). */
  failTime: number | null;
  /** Timestamp when link came up (for brightness pulse). */
  upTime: number | null;
  baseColor: THREE.Color;
  baseOpacity: number;
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

      let color: number;
      let width: number;
      let opacity: number;
      const dashed = ground;

      if (ground) {
        // VF spec 7.4: dashed, cyan, 2px
        color = LINK_GROUND_COLOR;
        width = LINK_GROUND_WIDTH;
        opacity = 0.6;
      } else {
        // VF spec 7.3, 10.2: solid, muted green, 1.5px
        color = LINK_ISL_COLOR;
        width = LINK_ISL_WIDTH;
        opacity = 0.55;
      }

      const material = new LineMaterial({
        color,
        linewidth: width,
        resolution,
        transparent: true,
        opacity,
        dashed,
        // VF spec 7.4: 16-unit dash, 8-unit gap for ground links
        dashScale: dashed ? 1 : 1,
        dashSize: dashed ? 16 : 1,
        gapSize: dashed ? 8 : 0,
        depthWrite: false,
      });

      const line = new Line2(geometry, material);
      line.userData["linkKey"] = key;
      line.userData["nodeA"] = ls.node_a;
      line.userData["nodeB"] = ls.node_b;
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
        failTime: null,
        upTime: now,
        baseColor: new THREE.Color(color),
        baseOpacity: opacity,
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
    if (entry.isGround) entry.line.computeLineDistances();

    // Fail-flash animation — boost opacity so it's visible on all link types
    if (entry.failTime !== null) {
      const elapsed = now - entry.failTime;
      if (elapsed < FAIL_HOLD_MS) {
        // Hold red at full opacity
        entry.material.color.setHex(LINK_FAIL_COLOR);
        entry.material.opacity = 0.7;
        entry.line.visible = true;
      } else if (elapsed < FAIL_HOLD_MS + FAIL_FADE_MS) {
        // Fade color to dark AND opacity to zero
        const t = (elapsed - FAIL_HOLD_MS) / FAIL_FADE_MS;
        const failColor = new THREE.Color(LINK_FAIL_COLOR);
        const darkColor = new THREE.Color(LINK_INACTIVE_COLOR);
        entry.material.color.copy(failColor).lerp(darkColor, t);
        entry.material.opacity = 0.7 * (1 - t);
        entry.line.visible = true;
      } else {
        // Hidden
        entry.line.visible = false;
        links.delete(key);
        continue;
      }
    } else if (entry.upTime !== null) {
      // Link-up pulse: bright base color → normal (0.75s), opacity boost
      const elapsed = now - entry.upTime;
      const UP_DURATION = 750;
      if (elapsed < UP_DURATION) {
        const t = elapsed / UP_DURATION;
        // Start from a brighter version of the base color, not white
        const bright = entry.baseColor.clone().multiplyScalar(2.5);
        bright.r = Math.min(bright.r, 1);
        bright.g = Math.min(bright.g, 1);
        bright.b = Math.min(bright.b, 1);
        entry.material.color.copy(bright).lerp(entry.baseColor, t);
        // Ease opacity from boosted down to base
        entry.material.opacity = 0.8 + (entry.baseOpacity - 0.8) * t;
      } else {
        entry.material.color.copy(entry.baseColor);
        entry.material.opacity = entry.baseOpacity;
        entry.upTime = null;
      }
      entry.line.visible = true;
    } else {
      entry.material.color.copy(entry.baseColor);
      entry.material.opacity = entry.baseOpacity;
      entry.line.visible = true;
    }
  }
}

export function getLinks(): Map<string, LinkEntry> {
  return links;
}
