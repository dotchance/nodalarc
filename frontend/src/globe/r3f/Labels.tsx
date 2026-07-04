// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/**
 * Labels — HTML DOM labels for satellites and ground stations, positioned in screen space by
 * projecting each node's WORLD position with the camera every frame. Reproduces the legacy
 * globe/labels.ts (satellite labels: updateLabels create/remove + animateLabels projection /
 * distance-fade / limb-outward offset) AND the GS-label half of globe/groundStations.ts
 * (updateGSLabels create/remove + projection / fade). It renders NO three.js objects (returns
 * null); it only consumes useFrame and a DOM container the integrator supplies via containerRef.
 *
 * Why DOM, not troika/drei: at a few hundred nodes the DOM cost is negligible and it gives
 * crisp text + CSS-token styling for free; the legacy globe uses the same approach. The
 * occlusion test is REUSED verbatim from globe/labels.ts (isOccludedByEarth, occ radius
 * EARTH_RADIUS_RENDER * 0.985 applied inside that fn), so a label never floats over the back
 * of the Earth. Positions come from the shared registry (getNodeWorldPosition) — never from
 * per-instance matrices — so labels track the same per-frame truth as the meshes/links.
 *
 * Mounted at scene-root (it needs the scene camera, not the Earth body frame). Default useFrame
 * priority (0) so it runs AFTER FrameDriver (-2) set the frame rotation and Constellation (-1)
 * wrote this frame's positions into the registry.
 */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { useFrame, useThree } from "@react-three/fiber";
import { isOccludedBySphere, getLabelsEnabled } from "../labels";
import { getGsLabelsEnabled } from "../groundStations";
import { getNodeBodySphere, getNodeWorldPosition } from "./positions";
import type { NodeState } from "../../types";
import { nodeDisplayLabel } from "../../networkIdentity";

// Satellite-label distance fade (globe/labels.ts FADE_IN_DIST / FADE_OUT_DIST).
const SAT_FADE_IN_DIST = 200;
const SAT_FADE_OUT_DIST = 500;

// Ground-station-label distance fade (globe/groundStations.ts GS_FADE_IN_DIST / GS_FADE_OUT_DIST).
const GS_FADE_IN_DIST = 200;
const GS_FADE_OUT_DIST = 500;

// A body whose projected radius is smaller than this folds its per-node labels
// into one body chip ("earth · 9 segments — zoom in"): collapse at the next
// higher layer instead of printing an unreadable pile.
const BODY_FOLD_SCREEN_RADIUS_PX = 90;

// Approximate label glyph metrics for the greedy de-overlap boxes. Cheap
// estimates are fine: the goal is no unreadable pile, not pixel-perfect
// packing.
const LABEL_CHAR_PX = 6.5;
const LABEL_HEIGHT_PX = 14;

// Per-frame projection temporaries — hoisted to module scope (zero useFrame heap alloc).
const _worldPos = new THREE.Vector3();
const _ndc = new THREE.Vector3();
const _bodyCenter = new THREE.Vector3();

interface PlacedRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Greedy overlap test against already-placed label boxes. Exported for tests. */
export function overlapsPlaced(rects: PlacedRect[], candidate: PlacedRect): boolean {
  for (const rect of rects) {
    if (
      candidate.x < rect.x + rect.w &&
      candidate.x + candidate.w > rect.x &&
      candidate.y < rect.y + rect.h &&
      candidate.y + candidate.h > rect.y
    ) {
      return true;
    }
  }
  return false;
}

/** Build the satellite label div (globe/labels.ts updateLabels — cssText copied so CSS vars resolve). */
function createSatLabel(label: string): HTMLDivElement {
  const div = document.createElement("div");
  div.className = "sat-label";
  div.textContent = label;
  div.style.cssText = `
    position: absolute;
    color: var(--text-primary);
    font-size: var(--font-size-xxs);
    pointer-events: none;
    white-space: nowrap;
    text-shadow: 0 0 4px rgba(0,0,0,0.95), 0 0 2px rgba(0,0,0,0.95);
    display: none;
  `;
  return div;
}

/** Build the ground-station label div (globe/groundStations.ts updateGroundStations — cssText copied). */
function createGsLabel(label: string): HTMLDivElement {
  const div = document.createElement("div");
  div.className = "gs-label";
  div.textContent = label;
  div.style.cssText = `
    position: absolute;
    color: var(--accent-teal);
    font-size: var(--font-size-xs);
    font-weight: var(--font-weight-bold);
    pointer-events: none;
    white-space: nowrap;
    text-shadow: 0 0 6px rgba(0,0,0,0.95), 0 0 2px rgba(0,0,0,0.95);
    background: var(--bg-scrim-light);
    padding: 1px 4px;
    border-radius: var(--radius-xs);
  `;
  return div;
}

interface LabelsProps {
  nodes: NodeState[];
  /** The position-absolute overlay div (sibling of the canvas) this component owns its labels in. */
  containerRef: React.RefObject<HTMLDivElement | null>;
  /** Selected ground station id - the stacked-site label follows it. */
  selectedGsId?: string | null;
  /** Selected node id — its label always wins de-overlap and body folding. */
  selectedNodeId?: string | null;
}

/** Build the folded-body chip div (one per small-on-screen body). */
function createBodyChip(): HTMLDivElement {
  const div = document.createElement("div");
  div.className = "body-fold-chip";
  div.style.cssText = `
    position: absolute;
    color: var(--text-secondary);
    font-size: var(--font-size-xs);
    pointer-events: none;
    white-space: nowrap;
    text-shadow: 0 0 6px rgba(0,0,0,0.95);
    background: var(--bg-scrim-light);
    padding: 1px 6px;
    border-radius: var(--radius-xs);
    display: none;
  `;
  return div;
}

/**
 * Multi-gateway sites stack at one surface point; rendering every member's
 * label produces unreadable overlap with an arbitrary winner. One label
 * represents the stack: the selected member when the selection is in the
 * site, the first member (sorted) otherwise.
 */
export function siteLabelRepresentatives(
  nodes: NodeState[],
  selectedGsId: string | null,
): Set<string> {
  const bySite = new Map<string, string[]>();
  for (const node of nodes) {
    if (node.node_type !== "ground_station") continue;
    const site = node.namespace ?? node.node_id;
    const members = bySite.get(site);
    if (members) members.push(node.node_id);
    else bySite.set(site, [node.node_id]);
  }
  const representatives = new Set<string>();
  for (const members of bySite.values()) {
    members.sort();
    representatives.add(
      selectedGsId !== null && members.includes(selectedGsId) ? selectedGsId : members[0]!,
    );
  }
  return representatives;
}

export function Labels({
  nodes,
  containerRef,
  selectedGsId = null,
  selectedNodeId = null,
}: LabelsProps) {
  const camera = useThree((s) => s.camera);
  const gsRepresentativesRef = useRef(new Set<string>());
  gsRepresentativesRef.current = siteLabelRepresentatives(nodes, selectedGsId);
  const selectedNodeIdRef = useRef(selectedNodeId);
  selectedNodeIdRef.current = selectedNodeId;

  // The label divs this component owns, keyed by node_id. Mutated in useFrame as nodes appear
  // / disappear, so read nodes through a ref to keep the useFrame closure stable (mirrors
  // Links.tsx kernelActualRef). One map per node type — they fade independently.
  const satLabels = useRef(new Map<string, HTMLDivElement>());
  const gsLabels = useRef(new Map<string, HTMLDivElement>());
  const bodyChips = useRef(new Map<string, HTMLDivElement>());
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;

  // Dispose: remove every owned div from the DOM on unmount (lifecycle ownership).
  useEffect(() => {
    const sats = satLabels.current;
    const gss = gsLabels.current;
    const chips = bodyChips.current;
    return () => {
      for (const div of sats.values()) div.remove();
      sats.clear();
      for (const div of gss.values()) div.remove();
      gss.clear();
      for (const div of chips.values()) div.remove();
      chips.clear();
    };
  }, []);

  useFrame(() => {
    const container = containerRef.current;
    if (!container) return;
    const nodeList = nodesRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;
    const cameraPos = camera.position;
    const cx = width / 2;
    const cy = height / 2;

    // Read the canonical label-toggle state each frame (the module-global the keyboard handler
    // writes via setLabelsEnabled / setGsLabelsEnabled, regardless of which globe is mounted) —
    // exactly as the legacy animateLabels / updateGSLabels do. No React state, no re-render.
    const satEnabled = getLabelsEnabled();
    const gsEnabled = getGsLabelsEnabled();

    // --- Reconcile divs: create for newly-seen nodes, remove for departed nodes. ---
    const seenSats = new Set<string>();
    const seenGs = new Set<string>();
    for (const node of nodeList) {
      if (node.node_type === "satellite") {
        seenSats.add(node.node_id);
        const label = nodeDisplayLabel(node);
        if (!satLabels.current.has(node.node_id)) {
          const div = createSatLabel(label);
          container.appendChild(div);
          satLabels.current.set(node.node_id, div);
        } else {
          satLabels.current.get(node.node_id)!.textContent = label;
        }
      } else if (node.node_type === "ground_station") {
        if (!gsRepresentativesRef.current.has(node.node_id)) continue;
        seenGs.add(node.node_id);
        const label = nodeDisplayLabel(node);
        if (!gsLabels.current.has(node.node_id)) {
          const div = createGsLabel(label);
          container.appendChild(div);
          gsLabels.current.set(node.node_id, div);
        } else {
          gsLabels.current.get(node.node_id)!.textContent = label;
        }
      }
    }
    for (const [id, div] of satLabels.current) {
      if (!seenSats.has(id)) {
        div.remove();
        satLabels.current.delete(id);
      }
    }
    for (const [id, div] of gsLabels.current) {
      if (!seenGs.has(id)) {
        div.remove();
        gsLabels.current.delete(id);
      }
    }

    // --- Body fold (collapse at the next higher layer). A body whose projected
    // radius is small on screen folds its per-node labels into one chip; the
    // selected node's label survives the fold (selection always wins).
    const selectedId = selectedNodeIdRef.current;
    const projectionFactor =
      height / (2 * Math.tan(((camera as THREE.PerspectiveCamera).fov * Math.PI) / 360));
    const foldedBodies = new Map<string, { sx: number; sy: number; segments: Set<string> }>();
    const bodySegments = new Map<string, Set<string>>();
    const bodyProbe = new Map<string, string>();
    for (const node of nodeList) {
      if (!node.reference_body) continue;
      let segments = bodySegments.get(node.reference_body);
      if (!segments) {
        segments = new Set();
        bodySegments.set(node.reference_body, segments);
        bodyProbe.set(node.reference_body, node.node_id);
      }
      if (node.segment_id) segments.add(node.segment_id);
    }
    for (const [body, probeId] of bodyProbe) {
      const sphere = getNodeBodySphere(probeId, _bodyCenter);
      if (!sphere) continue;
      const dist = _bodyCenter.distanceTo(cameraPos);
      if (dist < 1e-6) continue;
      const screenRadius = (sphere.radius * projectionFactor) / dist;
      if (screenRadius >= BODY_FOLD_SCREEN_RADIUS_PX) continue;
      _ndc.copy(_bodyCenter).project(camera);
      if (_ndc.z > 1) continue;
      foldedBodies.set(body, {
        sx: (_ndc.x * 0.5 + 0.5) * width,
        sy: (-_ndc.y * 0.5 + 0.5) * height,
        segments: bodySegments.get(body) ?? new Set(),
      });
    }
    const bodyByNode = new Map<string, string>();
    for (const node of nodeList) {
      if (node.reference_body) bodyByNode.set(node.node_id, node.reference_body);
    }

    // Reconcile chip divs against this frame's folded set.
    for (const [body, fold] of foldedBodies) {
      let chip = bodyChips.current.get(body);
      if (!chip) {
        chip = createBodyChip();
        container.appendChild(chip);
        bodyChips.current.set(body, chip);
      }
      chip.textContent = `${body} · ${fold.segments.size} segment${fold.segments.size === 1 ? "" : "s"} — zoom in`;
      chip.style.display = "block";
      chip.style.left = `${fold.sx + 12}px`;
      chip.style.top = `${fold.sy - 8}px`;
    }
    for (const [body, chip] of bodyChips.current) {
      if (!foldedBodies.has(body)) chip.style.display = "none";
    }

    // Greedy de-overlap boxes placed this frame (ground labels first, then
    // satellites; the selected node's label is placed before everything).
    const placedRects: PlacedRect[] = [];

    const isFolded = (id: string): boolean => {
      if (id === selectedId) return false;
      const body = bodyByNode.get(id);
      return body !== undefined && foldedBodies.has(body);
    };

    // --- Ground-station labels (globe/groundStations.ts updateGSLabels). ---
    const placeGsLabel = (id: string, div: HTMLDivElement): void => {
      if (isFolded(id) || !getNodeWorldPosition(id, _worldPos)) {
        div.style.display = "none";
        return;
      }
      const dist = _worldPos.distanceTo(cameraPos);
      if (dist > GS_FADE_OUT_DIST) {
        div.style.display = "none";
        return;
      }
      _ndc.copy(_worldPos).project(camera);
      if (_ndc.z > 1) {
        div.style.display = "none";
        return;
      }
      const bodySphere = getNodeBodySphere(id, _bodyCenter);
      if (bodySphere && isOccludedBySphere(
        _worldPos.x, _worldPos.y, _worldPos.z,
        cameraPos.x, cameraPos.y, cameraPos.z,
        _bodyCenter.x, _bodyCenter.y, _bodyCenter.z,
        bodySphere.radius,
      )) {
        div.style.display = "none";
        return;
      }

      const x = (_ndc.x * 0.5 + 0.5) * width;
      const y = (-_ndc.y * 0.5 + 0.5) * height;
      const rect: PlacedRect = {
        x: x + 8,
        y: y - 6,
        w: (div.textContent?.length ?? 8) * LABEL_CHAR_PX,
        h: LABEL_HEIGHT_PX,
      };
      if (id !== selectedId && overlapsPlaced(placedRects, rect)) {
        div.style.display = "none";
        return;
      }
      placedRects.push(rect);

      div.style.display = "block";
      div.style.left = `${rect.x}px`;
      div.style.top = `${rect.y}px`;

      if (dist < GS_FADE_IN_DIST) {
        div.style.opacity = "1";
      } else {
        const t = (dist - GS_FADE_IN_DIST) / (GS_FADE_OUT_DIST - GS_FADE_IN_DIST);
        div.style.opacity = String(1 - t * 0.7);
      }
    };

    // --- Satellite labels (globe/labels.ts animateLabels). ---
    const placeSatLabel = (id: string, div: HTMLDivElement): void => {
      if (isFolded(id) || !getNodeWorldPosition(id, _worldPos)) {
        div.style.display = "none";
        return;
      }
      const dist = _worldPos.distanceTo(cameraPos);
      if (dist > SAT_FADE_OUT_DIST) {
        div.style.display = "none";
        return;
      }
      _ndc.copy(_worldPos).project(camera);
      if (_ndc.z > 1) {
        div.style.display = "none";
        return;
      }
      const bodySphere = getNodeBodySphere(id, _bodyCenter);
      if (bodySphere && isOccludedBySphere(
        _worldPos.x, _worldPos.y, _worldPos.z,
        cameraPos.x, cameraPos.y, cameraPos.z,
        _bodyCenter.x, _bodyCenter.y, _bodyCenter.z,
        bodySphere.radius,
      )) {
        div.style.display = "none";
        return;
      }

      const sx = (_ndc.x * 0.5 + 0.5) * width;
      const sy = (-_ndc.y * 0.5 + 0.5) * height;

      // Offset away from screen center so limb labels point outward (animateLabels).
      const dx = sx - cx;
      const dy = sy - cy;
      const screenDist = Math.sqrt(dx * dx + dy * dy);
      const nx = screenDist > 1 ? dx / screenDist : 1;
      const ny = screenDist > 1 ? dy / screenDist : 0;

      const rect: PlacedRect = {
        x: sx + nx * 10,
        y: sy + ny * 10 - 6,
        w: (div.textContent?.length ?? 8) * LABEL_CHAR_PX,
        h: LABEL_HEIGHT_PX,
      };
      if (id !== selectedId && overlapsPlaced(placedRects, rect)) {
        div.style.display = "none";
        return;
      }
      placedRects.push(rect);

      div.style.display = "block";
      div.style.left = `${rect.x}px`;
      div.style.top = `${rect.y}px`;

      if (dist < SAT_FADE_IN_DIST) {
        div.style.opacity = "1";
        div.style.fontSize = "var(--font-size-xxs)";
        div.style.color = "var(--text-primary)";
      } else {
        const t = (dist - SAT_FADE_IN_DIST) / (SAT_FADE_OUT_DIST - SAT_FADE_IN_DIST);
        div.style.opacity = String(1 - t * 0.7);
        div.style.fontSize = "var(--font-size-xxs)";
        div.style.color = "var(--text-secondary)";
      }
    };

    // Selection always wins: its label claims screen space before any other.
    if (selectedId) {
      const selectedSat = satLabels.current.get(selectedId);
      if (selectedSat && satEnabled) placeSatLabel(selectedId, selectedSat);
      const selectedGs = gsLabels.current.get(selectedId);
      if (selectedGs && gsEnabled) placeGsLabel(selectedId, selectedGs);
    }

    if (!gsEnabled) {
      for (const div of gsLabels.current.values()) div.style.display = "none";
    } else {
      for (const [id, div] of gsLabels.current) {
        if (id === selectedId) continue;
        placeGsLabel(id, div);
      }
    }

    if (!satEnabled) {
      for (const div of satLabels.current.values()) div.style.display = "none";
    } else {
      for (const [id, div] of satLabels.current) {
        if (id === selectedId) continue;
        placeSatLabel(id, div);
      }
    }
  });

  return null;
}
