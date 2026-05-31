// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * FlowPaths — animated dashed fat-line polylines tracing StateSnapshot `traced_path`s
 * (one Line2 per flow, drawn through its hop node positions), ported faithfully from
 * globe/flowPaths.ts into the R3F lifecycle. The legacy module kept its flow Map and a
 * window `resize` listener at module scope and mutated a caller-supplied earthFrame; this
 * port owns the Map in a ref, mounts its Line2 objects into this component's <group> (a
 * child of the Earth body frame, so hop positions are correct in the earth-LOCAL frame),
 * and tracks the material resolution off the actual canvas size (useThree().size) rather
 * than the window. The legacy flowPaths.ts is left untouched for the live globe and deleted
 * at cutover — only one renders at a time.
 *
 * Behaviour reproduced verbatim: primary flow color LINK_FLOW_COLOR (first path) / secondary
 * LINK_FLOW_SECONDARY_COLOR (all others + every reverse path), LINK_FLOW_WIDTH px, dashed
 * { dashScale: 3, dashSize: 0.5, gapSize: 0.3 }, computeLineDistances() after every position
 * write, dashOffset decremented -0.01/frame (the "flow" animation), a path hidden when any
 * hop is unresolved (or fewer than two resolvable hops), and a reverse polyline drawn only
 * when reverse_hops.length > 0 && asymmetry_detected. Hop endpoints are re-resolved from the
 * registry every frame, so a flow tracks its propagating satellites with zero lag.
 *
 * Default useFrame priority (after FrameDriver -2 and Constellation -1) so the hop positions
 * it reads from the registry are this frame's.
 */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { LINK_FLOW_COLOR, LINK_FLOW_SECONDARY_COLOR, LINK_FLOW_WIDTH } from "../../config";
import { getNodeLocalPosition } from "./positions";
import type { TracedPath } from "../../types";

const DASH_SCALE = 3;
const DASH_SIZE = 0.5;
const GAP_SIZE = 0.3;
/** Per-frame dash advance — the "flow" animation (verbatim from globe/flowPaths.ts). */
const DASH_OFFSET_STEP = -0.01;

/** Hoisted hop temporary — zero per-frame allocation. */
const _flowHopPos = new THREE.Vector3();

interface FlowPathEntry {
  line: Line2;
  geometry: LineGeometry;
  material: LineMaterial;
  hops: string[];
  /** Reusable positions buffer for `hops` (3 floats per hop); regrown when hop count changes. */
  posBuffer: Float32Array;
  reverseLine?: Line2;
  reverseGeometry?: LineGeometry;
  reverseMaterial?: LineMaterial;
  reverseHops?: string[];
  reversePosBuffer?: Float32Array;
}

function makeMaterial(color: number, resolution: THREE.Vector2): LineMaterial {
  return new LineMaterial({
    color,
    linewidth: LINK_FLOW_WIDTH,
    resolution,
    dashed: true,
    dashScale: DASH_SCALE,
    dashSize: DASH_SIZE,
    gapSize: GAP_SIZE,
  });
}

/** Resize a buffer to hold `hopCount` xyz triples, reusing the old one when already sized. */
function sizeBuffer(existing: Float32Array | undefined, hopCount: number): Float32Array {
  const needed = hopCount * 3;
  if (existing && existing.length === needed) return existing;
  return new Float32Array(needed);
}

/** Whether a path has an asymmetric reverse leg to draw (verbatim guard from the legacy port). */
function hasReverse(path: TracedPath): boolean {
  return !!path.reverse_hops && path.reverse_hops.length > 0 && !!path.asymmetry_detected;
}

/**
 * Fill `buffer` (sized to hops.length*3) with each hop's earth-LOCAL position. Returns true
 * when the path should render, false when it must hide: any hop unresolved, or fewer than two
 * hops (no segment). Mirrors globe/flowPaths.ts collectHopPositions (which required
 * positions.length >= 6, i.e. >= 2 hops).
 */
function collectHopPositions(hops: string[], buffer: Float32Array): boolean {
  if (hops.length < 2) return false;
  let off = 0;
  for (const hop of hops) {
    if (!getNodeLocalPosition(hop, _flowHopPos)) return false;
    buffer[off++] = _flowHopPos.x;
    buffer[off++] = _flowHopPos.y;
    buffer[off++] = _flowHopPos.z;
  }
  return true;
}

interface FlowPathsProps {
  tracedPaths: TracedPath[];
}

export function FlowPaths({ tracedPaths }: FlowPathsProps) {
  const groupRef = useRef<THREE.Group>(null);
  const entriesRef = useRef(new Map<string, FlowPathEntry>());
  const size = useThree((s) => s.size);
  const sizeRef = useRef(size);
  sizeRef.current = size;

  // Reconcile the flow Map on each snapshot: create lines for new flows, update hop lists for
  // existing ones, dispose flows that left the snapshot. Color is index-based (first flow
  // primary, the rest secondary), matching globe/flowPaths.ts.
  useEffect(() => {
    const group = groupRef.current;
    if (!group) return;
    const entries = entriesRef.current;
    const resolution = new THREE.Vector2(sizeRef.current.width, sizeRef.current.height);
    const active = new Set<string>();

    let flowIndex = 0;
    for (const path of tracedPaths) {
      active.add(path.flow_id);
      const wantReverse = hasReverse(path);

      const existing = entries.get(path.flow_id);
      if (existing) {
        existing.hops = path.hops;
        existing.posBuffer = sizeBuffer(existing.posBuffer, path.hops.length);
        if (wantReverse) {
          existing.reverseHops = path.reverse_hops;
          // Lazily create the reverse line if the flow became asymmetric after first sighting.
          if (!existing.reverseLine) {
            const revGeometry = new LineGeometry();
            const revMaterial = makeMaterial(LINK_FLOW_SECONDARY_COLOR, resolution);
            const revLine = new Line2(revGeometry, revMaterial);
            revLine.computeLineDistances();
            group.add(revLine);
            existing.reverseLine = revLine;
            existing.reverseGeometry = revGeometry;
            existing.reverseMaterial = revMaterial;
          }
          existing.reversePosBuffer = sizeBuffer(existing.reversePosBuffer, path.reverse_hops!.length);
        } else {
          existing.reverseHops = undefined;
        }
        flowIndex++;
        continue;
      }

      const geometry = new LineGeometry();
      const material = makeMaterial(
        flowIndex === 0 ? LINK_FLOW_COLOR : LINK_FLOW_SECONDARY_COLOR,
        resolution,
      );
      const line = new Line2(geometry, material);
      line.computeLineDistances();
      group.add(line);

      const entry: FlowPathEntry = {
        line,
        geometry,
        material,
        hops: path.hops,
        posBuffer: sizeBuffer(undefined, path.hops.length),
      };

      if (wantReverse) {
        const revGeometry = new LineGeometry();
        const revMaterial = makeMaterial(LINK_FLOW_SECONDARY_COLOR, resolution);
        const revLine = new Line2(revGeometry, revMaterial);
        revLine.computeLineDistances();
        group.add(revLine);
        entry.reverseLine = revLine;
        entry.reverseGeometry = revGeometry;
        entry.reverseMaterial = revMaterial;
        entry.reverseHops = path.reverse_hops;
        entry.reversePosBuffer = sizeBuffer(undefined, path.reverse_hops!.length);
      }

      entries.set(path.flow_id, entry);
      flowIndex++;
    }

    // Dispose flows that left the snapshot.
    for (const [id, entry] of entries) {
      if (active.has(id)) continue;
      group.remove(entry.line);
      entry.geometry.dispose();
      entry.material.dispose();
      if (entry.reverseLine) {
        group.remove(entry.reverseLine);
        entry.reverseGeometry?.dispose();
        entry.reverseMaterial?.dispose();
      }
      entries.delete(id);
    }
  }, [tracedPaths]);

  // Track the canvas size so fat-line widths render correctly in split-pane layouts.
  useEffect(() => {
    for (const entry of entriesRef.current.values()) {
      entry.material.resolution.set(size.width, size.height);
      entry.reverseMaterial?.resolution.set(size.width, size.height);
    }
  }, [size]);

  // Dispose all lines on unmount.
  useEffect(() => {
    const entries = entriesRef.current;
    const group = groupRef.current;
    return () => {
      for (const entry of entries.values()) {
        if (group) group.remove(entry.line);
        entry.geometry.dispose();
        entry.material.dispose();
        if (entry.reverseLine) {
          if (group) group.remove(entry.reverseLine);
          entry.reverseGeometry?.dispose();
          entry.reverseMaterial?.dispose();
        }
      }
      entries.clear();
    };
  }, []);

  // Per-frame: re-resolve hop positions, upload, advance the dash offset. Default priority so
  // FrameDriver (-2) and Constellation (-1) have written this frame's positions/rotation first.
  useFrame(() => {
    for (const entry of entriesRef.current.values()) {
      if (collectHopPositions(entry.hops, entry.posBuffer)) {
        entry.geometry.setPositions(entry.posBuffer);
        entry.line.computeLineDistances();
        entry.line.visible = true;
        entry.material.dashOffset += DASH_OFFSET_STEP;
      } else {
        entry.line.visible = false;
      }

      if (entry.reverseLine && entry.reverseHops && entry.reverseHops.length > 0 && entry.reversePosBuffer) {
        if (
          collectHopPositions(entry.reverseHops, entry.reversePosBuffer) &&
          entry.reverseGeometry &&
          entry.reverseMaterial
        ) {
          entry.reverseGeometry.setPositions(entry.reversePosBuffer);
          entry.reverseLine.computeLineDistances();
          entry.reverseLine.visible = true;
          entry.reverseMaterial.dashOffset += DASH_OFFSET_STEP;
        } else {
          entry.reverseLine.visible = false;
        }
      } else if (entry.reverseLine) {
        entry.reverseLine.visible = false;
      }
    }
  });

  return <group ref={groupRef} />;
}
