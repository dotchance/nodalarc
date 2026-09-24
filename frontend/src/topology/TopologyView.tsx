// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** TopologyView — HTML5 Canvas 2D topology diagram with hover tooltips. */

import { useEffect, useRef, useCallback, useState } from "react";
import { computeLayout } from "./layout";
import { drawNode, drawAreaBounds, hitTestNode } from "./nodes";
import { drawLinks, hitTestLink, type FlowDrawing } from "./topoLinks";
import { TraceFades } from "../trace/traceFades";
import { drawsReverse, traceSegments } from "../trace/traceSegments";
import { setupInteraction, type ViewTransform } from "./interaction";
import { FAIL_HOLD_MS, FAIL_FADE_MS, TRACE_FORWARD_COLOR, TRACE_REVERSE_COLOR, hexToCSS } from "../config";
import { tokens } from "../styles/tokens";
import type { Regime } from "../taxonomy/regime";
import type { StateSnapshot, Selection, LinkState, ColorMode, NodeState } from "../types";
import {
  nodeInstance,
  roleLabel,
  routingSummary,
  type AreaColoring,
} from "../routing/instances";

/** Recently-removed link kept for fail-flash animation. */
interface FailedLink {
  link: LinkState;
  failTime: number;
}

interface TopologyViewProps {
  regimeById: ReadonlyMap<string, Regime>;
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  onFlyTo?: (nodeId: string) => void;
  colorMode?: ColorMode;
  /** Area colors of the chosen IS-IS or OSPF instance. */
  areaColoring: AreaColoring;
  showIslLinks?: boolean;
  showGroundLinks?: boolean;
}

/** The band of a node: its legend entry in the colored instance. */
function bandOf(areaColoring: AreaColoring): (node: NodeState) => string | null {
  return (node) => areaColoring.entryOf(node)?.label ?? null;
}

const FORWARD_STROKE = hexToCSS(TRACE_FORWARD_COLOR);
const REVERSE_STROKE = hexToCSS(TRACE_REVERSE_COLOR);

export function TopologyView({
  regimeById, snapshot, selection, onSelect, onFlyTo, colorMode = "area", areaColoring, showIslLinks = true, showGroundLinks = true }: TopologyViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const transformRef = useRef<ViewTransform>({ offsetX: 0, offsetY: 0, scale: 1 });
  const animFrameRef = useRef<number>(0);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const dashOffsetRef = useRef(0);
  const lastCanvasSizeRef = useRef({ w: 0, h: 0 });
  const [tooltipContent, setTooltipContent] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  // When the traced path is drawn: live, fading after it stopped, or not at all.
  const traceFadesRef = useRef(new TraceFades());

  // Fail-flash: track links that disappeared from the snapshot
  const prevLinkKeysRef = useRef<Set<string>>(new Set());
  const prevLinksRef = useRef<Map<string, LinkState>>(new Map());
  const failedLinksRef = useRef<Map<string, FailedLink>>(new Map());

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !snapshot) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Resize canvas to container
    const rect = canvas.getBoundingClientRect();
    if (canvas.width !== rect.width || canvas.height !== rect.height) {
      canvas.width = rect.width;
      canvas.height = rect.height;
    }

    // Detect removed links and add to fail-flash set
    const now = performance.now();
    const currentKeys = new Set(
      snapshot.links.map((l) => [l.node_a, l.node_b].sort().join(":")),
    );
    const currentLinksMap = new Map(
      snapshot.links.map((l) => [[l.node_a, l.node_b].sort().join(":"), l] as const),
    );
    for (const key of prevLinkKeysRef.current) {
      if (!currentKeys.has(key)) {
        const oldLink = prevLinksRef.current.get(key);
        if (oldLink) {
          failedLinksRef.current.set(key, { link: oldLink, failTime: now });
        }
      }
    }
    prevLinkKeysRef.current = currentKeys;
    prevLinksRef.current = new Map(currentLinksMap);

    // Expire old failed links
    const expiry = FAIL_HOLD_MS + FAIL_FADE_MS;
    for (const [key, fl] of failedLinksRef.current) {
      if (now - fl.failTime > expiry) failedLinksRef.current.delete(key);
    }

    // Merge active + failed links for layout
    const mergedLinks: LinkState[] = [...snapshot.links];
    for (const [, fl] of failedLinksRef.current) {
      mergedLinks.push({ ...fl.link, state: "failed" });
    }

    const layout = computeLayout(snapshot.nodes, mergedLinks, bandOf(areaColoring));

    // Auto-center layout when canvas becomes visible or resizes significantly
    const cw = canvas.width;
    const ch = canvas.height;
    const prev = lastCanvasSizeRef.current;
    if (layout.nodes.length > 0 && cw > 0 && ch > 0 &&
        (prev.w === 0 || prev.h === 0 || Math.abs(cw - prev.w) > 50 || Math.abs(ch - prev.h) > 50)) {
      lastCanvasSizeRef.current = { w: cw, h: ch };
      const toolbarWidth = 60; // toolbar ~48px + margin
      const availW = cw - toolbarWidth;
      const availH = ch;
      const scale = Math.min(availW / layout.width, availH / layout.height, 4);
      const offsetX = toolbarWidth + (availW - layout.width * scale) / 2;
      const offsetY = (availH - layout.height * scale) / 2;
      transformRef.current = { offsetX, offsetY, scale };
    }

    const t = transformRef.current;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.translate(t.offsetX, t.offsetY);
    ctx.scale(t.scale, t.scale);

    // Build node map for link drawing
    const nodeMap = new Map(layout.nodes.map((n) => [n.id, n]));

    // Animate flow path dash
    dashOffsetRef.current = (dashOffsetRef.current + 0.5) % 18;

    // Draw area bounding boxes (behind everything)
    drawAreaBounds(ctx, layout.areas, (band) => {
      const entry = areaColoring.legend.find((item) => item.label === band);
      if (entry === undefined) throw new Error(`topology band ${band} is not in the area legend`);
      return entry.color;
    });

    // Draw links first (below nodes)
    traceFadesRef.current.observe(snapshot.traced_paths, now);
    const drawnTrace = traceFadesRef.current.drawn(now)[0] ?? null;
    // Each drawn leg with its color and where its hop numbers sit: forward above
    // the node, reverse below it.
    const legs: { hops: readonly string[]; color: string; labelDy: number }[] = [];
    if (drawnTrace) {
      legs.push({ hops: drawnTrace.path.hops, color: FORWARD_STROKE, labelDy: -10 });
      if (drawsReverse(drawnTrace.path)) {
        legs.push({ hops: drawnTrace.path.reverse_hops, color: REVERSE_STROKE, labelDy: 18 });
      }
    }
    const flows: FlowDrawing[] = legs.map((leg) => ({
      segments: traceSegments(leg.hops, (hop) => nodeMap.has(hop)),
      color: leg.color,
      opacity: drawnTrace!.opacity,
      animate: drawnTrace!.animate,
    }));
    // Build failTimes map for drawLinks fade animation
    const failTimes = new Map<string, number>();
    for (const [key, fl] of failedLinksRef.current) {
      failTimes.set(key, fl.failTime);
    }
    drawLinks(ctx, layout.links, nodeMap, flows, dashOffsetRef.current, failTimes, showIslLinks, showGroundLinks);

    // Hop numbers on each leg's nodes, as traceroute counts them: the source is 0.
    if (drawnTrace) {
      ctx.save();
      ctx.globalAlpha = drawnTrace.opacity;
      ctx.font = `9px ${tokens.fontFamilyCli}`;
      ctx.textAlign = "left";
      for (const leg of legs) {
        ctx.fillStyle = leg.color;
        leg.hops.forEach((hop, index) => {
          const hopNode = nodeMap.get(hop);
          if (hopNode) ctx.fillText(String(index), hopNode.x + 12, hopNode.y + leg.labelDy);
        });
      }
      ctx.restore();
    }

    // Isolated nodes have no active links; the ABR badge marks the colored
    // instance's area border routers.
    const connectedNodes = new Set<string>();
    for (const l of layout.links) {
      connectedNodes.add(l.nodeA);
      connectedNodes.add(l.nodeB);
    }
    const stateById = new Map(snapshot.nodes.map((n) => [n.node_id, n]));
    const coloredDomain = areaColoring.instance?.domainId ?? null;

    for (const node of layout.nodes) {
      const state = stateById.get(node.id)!;
      const isSelected = selection?.id === node.id;
      const isIsolated = !connectedNodes.has(node.id);
      const isABR =
        coloredDomain !== null && nodeInstance(state, coloredDomain)?.area_border === true;
      drawNode(
        ctx,
        node,
        isSelected,
        isIsolated,
        isABR,
        colorMode,
        regimeById.get(node.id),
        areaColoring.colorOf(state),
      );
    }

    ctx.restore();

    animFrameRef.current = requestAnimationFrame(draw);
  }, [snapshot, selection, showIslLinks, showGroundLinks, colorMode, regimeById, areaColoring]);

  useEffect(() => {
    animFrameRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [draw]);

  // Setup pan/zoom/click/hover interaction
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const cleanup = setupInteraction(
      canvas,
      () => transformRef.current,
      (t) => { transformRef.current = t; },
      (worldX, worldY) => {
        if (!snapshot) return;
        const layout = computeLayout(snapshot.nodes, snapshot.links, bandOf(areaColoring));
        const nodeMap = new Map(layout.nodes.map((n) => [n.id, n]));

        // Test nodes first, then links
        const hitNode = hitTestNode(worldX, worldY, layout.nodes);
        if (hitNode) {
          onSelect({
            type: hitNode.type === "ground_station" ? "ground_station" : "satellite",
            id: hitNode.id,
          });
          onFlyTo?.(hitNode.id);
          return;
        }

        const hitLink = hitTestLink(worldX, worldY, layout.links, nodeMap);
        if (hitLink) {
          const key = `${hitLink.nodeA}:${hitLink.nodeB}`;
          onSelect({ type: "link", id: key });
          return;
        }

        onSelect(null);
      },
      // Hover callback
      (worldX, worldY) => {
        if (!snapshot) {
          setTooltipContent(null);
          return;
        }
        const layout = computeLayout(snapshot.nodes, snapshot.links, bandOf(areaColoring));
        const nodeMap = new Map(layout.nodes.map((n) => [n.id, n]));

        const hitNode = hitTestNode(worldX, worldY, layout.nodes);
        if (hitNode) {
          const t = transformRef.current;
          const rect = canvas.getBoundingClientRect();
          setTooltipPos({
            x: worldX * t.scale + t.offsetX + rect.left + 12,
            y: worldY * t.scale + t.offsetY + rect.top - 8,
          });
          const nodeState = snapshot.nodes.find((n) => n.node_id === hitNode.id);
          if (nodeState && nodeState.node_type === "satellite") {
            setTooltipContent(
              `${hitNode.id}: ${roleLabel(nodeState.role)}, ${nodeState.isl_count} ISLs, ${nodeState.gnd_count} GND, ${routingSummary(nodeState)}`,
            );
          } else if (nodeState) {
            const prefix = nodeState.prefix ? `, ${nodeState.prefix}` : "";
            const activeLinks = snapshot.links.filter(
              (l) => (l.node_a === hitNode.id || l.node_b === hitNode.id) && l.state === "active",
            ).length;
            setTooltipContent(
              `${hitNode.id}: ${activeLinks} active links${prefix}`,
            );
          }
          return;
        }

        const hitLink = hitTestLink(worldX, worldY, layout.links, nodeMap);
        if (hitLink) {
          const t = transformRef.current;
          const rect = canvas.getBoundingClientRect();
          setTooltipPos({
            x: worldX * t.scale + t.offsetX + rect.left + 12,
            y: worldY * t.scale + t.offsetY + rect.top - 8,
          });
          const linkState = snapshot.links.find(
            (l) =>
              (l.node_a === hitLink.nodeA && l.node_b === hitLink.nodeB) ||
              (l.node_a === hitLink.nodeB && l.node_b === hitLink.nodeA),
          );
          if (linkState) {
            setTooltipContent(
              `${linkState.node_a} \u2194 ${linkState.node_b}: ${linkState.latency_ms.toFixed(1)}ms, ${linkState.state}`,
            );
          }
          return;
        }

        setTooltipContent(null);
      },
    );

    return cleanup;
  }, [snapshot, onSelect, onFlyTo, areaColoring]);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <canvas
        ref={canvasRef}
        className="topology-view"
      />
      {tooltipContent && (
        <div
          ref={tooltipRef}
          className="topo-tooltip"
          style={{
            position: "fixed",
            left: tooltipPos.x,
            top: tooltipPos.y,
          }}
        >
          {tooltipContent}
        </div>
      )}
    </div>
  );
}
