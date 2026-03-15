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
  reverseLine?: Line2;
  reverseGeometry?: LineGeometry;
  reverseMaterial?: LineMaterial;
  reverseHops?: string[];
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
      const entry = flowPaths.get(path.flow_id)!;
      entry.hops = path.hops;
      // Update reverse path hops
      if (path.reverse_hops && path.reverse_hops.length > 0 && path.asymmetry_detected) {
        entry.reverseHops = path.reverse_hops;
      } else {
        entry.reverseHops = undefined;
      }
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

    const flowEntry: FlowPathEntry = { line, geometry, material, hops: path.hops };

    // Create reverse path line if asymmetric
    if (path.reverse_hops && path.reverse_hops.length > 0 && path.asymmetry_detected) {
      const revGeometry = new LineGeometry();
      revGeometry.setPositions(new Array(path.reverse_hops.length * 3).fill(0));
      const revMaterial = new LineMaterial({
        color: LINK_FLOW_SECONDARY_COLOR,
        linewidth: LINK_FLOW_WIDTH,
        resolution,
        dashed: true,
        dashScale: 3,
        dashSize: 0.5,
        gapSize: 0.3,
      });
      const revLine = new Line2(revGeometry, revMaterial);
      revLine.computeLineDistances();
      scene.add(revLine);
      flowEntry.reverseLine = revLine;
      flowEntry.reverseGeometry = revGeometry;
      flowEntry.reverseMaterial = revMaterial;
      flowEntry.reverseHops = path.reverse_hops;
    }

    flowPaths.set(path.flow_id, flowEntry);
    flowIndex++;
  }

  // Remove old flow paths
  for (const [id, entry] of flowPaths) {
    if (!active.has(id)) {
      scene.remove(entry.line);
      entry.geometry.dispose();
      entry.material.dispose();
      if (entry.reverseLine) {
        scene.remove(entry.reverseLine);
        entry.reverseGeometry?.dispose();
        entry.reverseMaterial?.dispose();
      }
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

    // Animate reverse path
    if (entry.reverseLine && entry.reverseHops && entry.reverseHops.length > 0) {
      const revPositions: number[] = [];
      let revValid = true;
      for (const hop of entry.reverseHops) {
        const pos = sats.get(hop)?.mesh.position ?? gss.get(hop)?.sprite.position;
        if (!pos) {
          revValid = false;
          break;
        }
        revPositions.push(pos.x, pos.y, pos.z);
      }
      if (revValid && revPositions.length >= 6) {
        entry.reverseGeometry!.setPositions(revPositions);
        entry.reverseLine.computeLineDistances();
        entry.reverseLine.visible = true;
        entry.reverseMaterial!.dashOffset -= 0.01;
      } else {
        entry.reverseLine.visible = false;
      }
    } else if (entry.reverseLine) {
      entry.reverseLine.visible = false;
    }
  }
}
