// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** TopologyView — HTML5 Canvas 2D topology diagram with hover tooltips. */

import { useEffect, useRef, useCallback, useState } from "react";
import { computeLayout } from "./layout";
import { drawNode, drawAreaBounds, hitTestNode } from "./nodes";
import { drawLinks, hitTestLink } from "./topoLinks";
import { setupInteraction, type ViewTransform } from "./interaction";
import { FAIL_HOLD_MS, FAIL_FADE_MS } from "../config";
import type { StateSnapshot, Selection, LinkState, ColorMode } from "../types";

/** Recently-removed link kept for fail-flash animation. */
interface FailedLink {
  link: LinkState;
  failTime: number;
}

interface TopologyViewProps {
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  onFlyTo?: (nodeId: string) => void;
  colorMode?: ColorMode;
  showIslLinks?: boolean;
  showGroundLinks?: boolean;
}

export function TopologyView({ snapshot, selection, onSelect, onFlyTo, colorMode = "area", showIslLinks = true, showGroundLinks = true }: TopologyViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const transformRef = useRef<ViewTransform>({ offsetX: 0, offsetY: 0, scale: 1 });
  const animFrameRef = useRef<number>(0);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const dashOffsetRef = useRef(0);
  const lastCanvasSizeRef = useRef({ w: 0, h: 0 });
  const [tooltipContent, setTooltipContent] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

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

    const layout = computeLayout(snapshot.nodes, mergedLinks);

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
    drawAreaBounds(ctx, layout.areas);

    // Draw links first (below nodes)
    const flowPath = snapshot.traced_paths.length > 0
      ? snapshot.traced_paths[0]!.hops
      : null;
    // Build failTimes map for drawLinks fade animation
    const failTimes = new Map<string, number>();
    for (const [key, fl] of failedLinksRef.current) {
      failTimes.set(key, fl.failTime);
    }
    drawLinks(ctx, layout.links, nodeMap, flowPath, dashOffsetRef.current, failTimes, showIslLinks, showGroundLinks);

    // Draw hop index numbers on flow path nodes
    if (flowPath && flowPath.length >= 2) {
      ctx.fillStyle = "#ff8800";
      ctx.font = "9px monospace";
      ctx.textAlign = "left";
      for (let i = 0; i < flowPath.length; i++) {
        const hopNode = nodeMap.get(flowPath[i]!);
        if (!hopNode) continue;
        ctx.fillText(String(i + 1), hopNode.x + 12, hopNode.y - 10);
      }
    }

    // Determine isolated nodes (no active links) and ABR nodes (links in multiple areas)
    const connectedNodes = new Set<string>();
    const nodeAreas = new Map<string, Set<string>>();
    for (const l of layout.links) {
      connectedNodes.add(l.nodeA);
      connectedNodes.add(l.nodeB);
      // Track areas each node has links to
      const areaA = nodeMap.get(l.nodeA)?.area;
      const areaB = nodeMap.get(l.nodeB)?.area;
      if (areaA && areaB) {
        if (!nodeAreas.has(l.nodeA)) nodeAreas.set(l.nodeA, new Set());
        if (!nodeAreas.has(l.nodeB)) nodeAreas.set(l.nodeB, new Set());
        nodeAreas.get(l.nodeA)!.add(areaA).add(areaB);
        nodeAreas.get(l.nodeB)!.add(areaA).add(areaB);
      }
    }
    const abrNodes = new Set<string>();
    for (const [id, areas] of nodeAreas) {
      if (areas.size > 1) abrNodes.add(id);
    }

    for (const node of layout.nodes) {
      const isSelected = selection?.id === node.id;
      const isIsolated = !connectedNodes.has(node.id);
      drawNode(ctx, node, isSelected, isIsolated, abrNodes.has(node.id), colorMode);
    }

    ctx.restore();

    animFrameRef.current = requestAnimationFrame(draw);
  }, [snapshot, selection, showIslLinks, showGroundLinks]);

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
        const layout = computeLayout(snapshot.nodes, snapshot.links);
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
        const layout = computeLayout(snapshot.nodes, snapshot.links);
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
            const connectedLinks = snapshot.links.filter(
              (l) => l.node_a === hitNode.id || l.node_b === hitNode.id,
            );
            const linkedAreas = new Set<string>();
            if (nodeState.routing_area) linkedAreas.add(nodeState.routing_area);
            for (const l of connectedLinks) {
              const peerId = l.node_a === hitNode.id ? l.node_b : l.node_a;
              const peer = snapshot.nodes.find((n) => n.node_id === peerId);
              if (peer?.routing_area) linkedAreas.add(peer.routing_area);
            }
            const abrTag = linkedAreas.size > 1 ? " [ABR]" : "";
            setTooltipContent(
              `${hitNode.id}: ${nodeState.isl_count} ISLs, ${nodeState.gnd_count} GND, Area ${nodeState.routing_area ?? "?"}${abrTag}`,
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
  }, [snapshot, onSelect]);

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
