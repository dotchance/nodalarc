/** Flow path visualization — animated dashed Line2 in orange. */

import * as THREE from "three";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LINK_FLOW_COLOR, LINK_FLOW_SECONDARY_COLOR, LINK_FLOW_WIDTH } from "../config";
import { getSatellites } from "./satellites";
import { getGroundStations } from "./groundStations";
import type { TracedPath } from "../types";

interface FlowPathEntry {
  line: Line2;
  geometry: LineGeometry;
  material: LineMaterial;
  hops: string[];
}

const flowPaths = new Map<string, FlowPathEntry>();
const resolution = new THREE.Vector2(window.innerWidth, window.innerHeight);

window.addEventListener("resize", () => {
  resolution.set(window.innerWidth, window.innerHeight);
  for (const entry of flowPaths.values()) {
    entry.material.resolution.copy(resolution);
  }
});

export function updateFlowPaths(paths: TracedPath[], scene: THREE.Scene): void {
  const active = new Set<string>();

  let flowIndex = 0;
  for (const path of paths) {
    active.add(path.flow_id);

    if (flowPaths.has(path.flow_id)) {
      flowPaths.get(path.flow_id)!.hops = path.hops;
      flowIndex++;
      continue;
    }

    const geometry = new LineGeometry();
    const positions = new Array(path.hops.length * 3).fill(0);
    geometry.setPositions(positions);

    const material = new LineMaterial({
      color: flowIndex === 0 ? LINK_FLOW_COLOR : LINK_FLOW_SECONDARY_COLOR,
      linewidth: LINK_FLOW_WIDTH,
      resolution,
      dashed: true,
      dashScale: 3,
      dashSize: 0.5,
      gapSize: 0.3,
    });

    const line = new Line2(geometry, material);
    line.computeLineDistances();
    scene.add(line);

    flowPaths.set(path.flow_id, { line, geometry, material, hops: path.hops });
    flowIndex++;
  }

  // Remove old flow paths
  for (const [id, entry] of flowPaths) {
    if (!active.has(id)) {
      scene.remove(entry.line);
      entry.geometry.dispose();
      entry.material.dispose();
      flowPaths.delete(id);
    }
  }
}

export function animateFlowPaths(): void {
  const sats = getSatellites();
  const gss = getGroundStations();

  for (const entry of flowPaths.values()) {
    const positions: number[] = [];
    let valid = true;

    for (const hop of entry.hops) {
      const pos = sats.get(hop)?.mesh.position ?? gss.get(hop)?.sprite.position;
      if (!pos) {
        valid = false;
        break;
      }
      positions.push(pos.x, pos.y, pos.z);
    }

    if (valid && positions.length >= 6) {
      entry.geometry.setPositions(positions);
      entry.line.computeLineDistances();
      entry.line.visible = true;
      // Animate dash offset
      entry.material.dashOffset -= 0.01;
    } else {
      entry.line.visible = false;
    }
  }
}
