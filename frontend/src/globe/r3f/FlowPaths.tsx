// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * FlowPaths — the StateSnapshot's traced paths drawn through their hops' world positions.
 *
 * Each direction draws by the shared trace rule (trace/traceSegments.ts): measured segments
 * between consecutive placed hops as animated dashed fat lines, and bridged segments across
 * hops that cannot be placed (a silent hop, an unowned address, a node the view does not show)
 * as a thin dotted line in their own color. A forward path takes TRACE_FORWARD_COLOR and a
 * reverse path TRACE_REVERSE_COLOR, as in the trace dialog; a reverse path is drawn when the
 * trace measured asymmetry. Every segment takes the shape the link renderer gives the same
 * pair (linkCurve.ts): straight when either end is a ground station, the ISL arc otherwise, and
 * a curve along a body's surface where that shape would enter the body. When a trace is drawn,
 * and how opaque, comes from the shared
 * TraceFades (trace/traceFades.ts): a stopped trace stops animating and fades out the way a
 * failed link does.
 *
 * Hop positions are re-resolved from the position registry every frame in WORLD space, so a
 * flow tracks propagating satellites and inter-body paths with zero lag. Default useFrame
 * priority (after FrameDriver -2 and Constellation -1) so the positions it reads are this
 * frame's.
 */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import {
  TRACE_BRIDGED_COLOR,
  TRACE_BRIDGED_WIDTH,
  TRACE_FORWARD_COLOR,
  TRACE_REVERSE_COLOR,
  TRACE_WIDTH,
} from "../../config";
import { TraceFades } from "../../trace/traceFades";
import { drawsReverse, traceSegments } from "../../trace/traceSegments";
import { bodyWorldSpheres, getNodeWorldPosition } from "./positions";
import { MAX_TRACE_LINE_POINTS, traceLinePoints, type BodySphere } from "./linkCurve";
import { SegmentLineBuffer } from "./segmentLineBuffer";
import type { TracedPath } from "../../types";

const DASH_SCALE = 3;
const DASH_SIZE = 0.5;
const GAP_SIZE = 0.3;
// Bridged segments: short dots with long gaps, so they never read as a measured link.
const BRIDGED_DASH_SIZE = 0.08;
const BRIDGED_GAP_SIZE = 0.35;
/** Per-frame dash advance — the "flow" animation of a live trace. */
const DASH_OFFSET_STEP = -0.01;

/** One style of segments for one direction: its line, material and GPU buffers. */
interface SegmentLine {
  line: LineSegments2;
  material: LineMaterial;
  buffer: SegmentLineBuffer;
}

interface DirectionLines {
  hops: string[];
  measured: SegmentLine;
  bridged: SegmentLine;
  /** Reused every frame: the placed hops' world positions, from a pool of vectors. */
  placed: Map<string, THREE.Vector3>;
  pool: THREE.Vector3[];
}

interface FlowPathEntry {
  forward: DirectionLines;
  reverse: DirectionLines | null;
}

function makeSegmentLine(
  group: THREE.Group,
  color: number,
  width: number,
  dashSize: number,
  gapSize: number,
  resolution: THREE.Vector2,
): SegmentLine {
  const buffer = new SegmentLineBuffer();
  const material = new LineMaterial({
    color,
    linewidth: width,
    resolution,
    dashed: true,
    dashScale: DASH_SCALE,
    dashSize,
    gapSize,
    transparent: true,
  });
  const line = new LineSegments2(buffer.geometry, material);
  line.frustumCulled = false;
  line.visible = false;
  group.add(line);
  return { line, material, buffer };
}

function makeDirection(
  group: THREE.Group,
  hops: string[],
  color: number,
  resolution: THREE.Vector2,
): DirectionLines {
  return {
    hops,
    placed: new Map(),
    pool: [],
    measured: makeSegmentLine(group, color, TRACE_WIDTH, DASH_SIZE, GAP_SIZE, resolution),
    bridged: makeSegmentLine(
      group,
      TRACE_BRIDGED_COLOR,
      TRACE_BRIDGED_WIDTH,
      BRIDGED_DASH_SIZE,
      BRIDGED_GAP_SIZE,
      resolution,
    ),
  };
}

function disposeSegmentLine(segmentLine: SegmentLine, group: THREE.Group | null): void {
  group?.remove(segmentLine.line);
  segmentLine.buffer.dispose();
  segmentLine.material.dispose();
}

function disposeDirection(direction: DirectionLines | null, group: THREE.Group | null): void {
  if (!direction) return;
  disposeSegmentLine(direction.measured, group);
  disposeSegmentLine(direction.bridged, group);
}

function disposeEntry(entry: FlowPathEntry, group: THREE.Group | null): void {
  disposeDirection(entry.forward, group);
  disposeDirection(entry.reverse, group);
}

/** Place the direction's hops for this frame; a hop that cannot be placed is absent. */
function placeHops(direction: DirectionLines): void {
  direction.placed.clear();
  let used = 0;
  for (const hop of direction.hops) {
    if (direction.placed.has(hop)) continue;
    const position = (direction.pool[used] ??= new THREE.Vector3());
    if (getNodeWorldPosition(hop, position)) {
      direction.placed.set(hop, position);
      used++;
    }
  }
}

/** Reused every frame: one trace segment's drawn points. */
const _linePoints = Array.from({ length: MAX_TRACE_LINE_POINTS }, () => new THREE.Vector3());

/** Write one style's segments into its line; hide the line when there are none. */
function drawSegments(
  segmentLine: SegmentLine,
  segments: { from: string; to: string }[],
  placed: Map<string, THREE.Vector3>,
  isGround: (nodeId: string) => boolean,
  bodies: readonly BodySphere[],
  opacity: number,
  animate: boolean,
): void {
  if (segments.length === 0) {
    segmentLine.line.visible = false;
    return;
  }
  const buffer = segmentLine.buffer;
  if (buffer.begin(segments.length * (MAX_TRACE_LINE_POINTS - 1))) {
    segmentLine.line.geometry = buffer.geometry;
  }
  for (const segment of segments) {
    const count = traceLinePoints(
      placed.get(segment.from)!,
      placed.get(segment.to)!,
      isGround(segment.from) || isGround(segment.to),
      bodies,
      _linePoints,
    );
    for (let i = 0; i + 1 < count; i++) buffer.push(_linePoints[i]!, _linePoints[i + 1]!);
  }
  buffer.end();
  segmentLine.material.opacity = opacity;
  if (animate) segmentLine.material.dashOffset += DASH_OFFSET_STEP;
  segmentLine.line.visible = true;
}

function drawDirection(
  direction: DirectionLines,
  isGround: (nodeId: string) => boolean,
  bodies: readonly BodySphere[],
  opacity: number,
  animate: boolean,
): void {
  placeHops(direction);
  const placed = direction.placed;
  const segments = traceSegments(direction.hops, (hop) => placed.has(hop));
  drawSegments(
    direction.measured,
    segments.filter((segment) => segment.measured),
    placed,
    isGround,
    bodies,
    opacity,
    animate,
  );
  drawSegments(
    direction.bridged,
    segments.filter((segment) => !segment.measured),
    placed,
    isGround,
    bodies,
    opacity,
    false,
  );
}

interface FlowPathsProps {
  tracedPaths: TracedPath[];
  /** Ground station node ids: a segment touching one is drawn straight, like a ground link. */
  groundNodeIds: ReadonlySet<string>;
}

export function FlowPaths({ tracedPaths, groundNodeIds }: FlowPathsProps) {
  const groupRef = useRef<THREE.Group>(null);
  const entriesRef = useRef(new Map<string, FlowPathEntry>());
  const fadesRef = useRef(new TraceFades());
  const size = useThree((s) => s.size);
  const sizeRef = useRef(size);
  sizeRef.current = size;
  const groundNodeIdsRef = useRef(groundNodeIds);
  groundNodeIdsRef.current = groundNodeIds;

  useEffect(() => {
    fadesRef.current.observe(tracedPaths, performance.now());
  }, [tracedPaths]);

  // Track the canvas size so fat-line widths render correctly in split-pane layouts.
  useEffect(() => {
    for (const entry of entriesRef.current.values()) {
      for (const direction of [entry.forward, entry.reverse]) {
        if (!direction) continue;
        direction.measured.material.resolution.set(size.width, size.height);
        direction.bridged.material.resolution.set(size.width, size.height);
      }
    }
  }, [size]);

  // Dispose all lines on unmount.
  useEffect(() => {
    const entries = entriesRef.current;
    const group = groupRef.current;
    return () => {
      for (const entry of entries.values()) disposeEntry(entry, group);
      entries.clear();
    };
  }, []);

  // Each frame: draw the traces TraceFades says are drawn, creating their lines on first
  // draw, and dispose the lines of any trace no longer drawn.
  useFrame(() => {
    const group = groupRef.current;
    if (!group) return;
    const entries = entriesRef.current;
    const resolution = () => new THREE.Vector2(sizeRef.current.width, sizeRef.current.height);
    const drawnIds = new Set<string>();
    const isGround = (nodeId: string) => groundNodeIdsRef.current.has(nodeId);
    const bodies = bodyWorldSpheres();
    fadesRef.current.drawn(performance.now()).forEach(({ path, opacity, animate }) => {
      drawnIds.add(path.flow_id);
      let entry = entries.get(path.flow_id);
      if (!entry) {
        entry = {
          forward: makeDirection(group, path.hops, TRACE_FORWARD_COLOR, resolution()),
          reverse: null,
        };
        entries.set(path.flow_id, entry);
      }
      entry.forward.hops = path.hops;
      if (drawsReverse(path)) {
        entry.reverse ??= makeDirection(group, path.reverse_hops, TRACE_REVERSE_COLOR, resolution());
        entry.reverse.hops = path.reverse_hops;
      } else {
        disposeDirection(entry.reverse, group);
        entry.reverse = null;
      }
      drawDirection(entry.forward, isGround, bodies, opacity, animate);
      if (entry.reverse) drawDirection(entry.reverse, isGround, bodies, opacity, animate);
    });
    for (const [flowId, entry] of entries) {
      if (drawnIds.has(flowId)) continue;
      disposeEntry(entry, group);
      entries.delete(flowId);
    }
  });

  return <group ref={groupRef} />;
}
