/** TopologyView — HTML5 Canvas 2D topology diagram with hover tooltips. */

import { useEffect, useRef, useCallback, useState } from "react";
import { computeLayout } from "./layout";
import { drawNode, hitTestNode } from "./nodes";
import { drawLinks, hitTestLink } from "./topoLinks";
import { setupInteraction, type ViewTransform } from "./interaction";
import type { StateSnapshot, Selection } from "../types";

interface TopologyViewProps {
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
  onFlyTo?: (nodeId: string) => void;
}

export function TopologyView({ snapshot, selection, onSelect, onFlyTo }: TopologyViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const transformRef = useRef<ViewTransform>({ offsetX: 0, offsetY: 0, scale: 1 });
  const animFrameRef = useRef<number>(0);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const dashOffsetRef = useRef(0);
  const lastCanvasSizeRef = useRef({ w: 0, h: 0 });
  const [tooltipContent, setTooltipContent] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

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

    const layout = computeLayout(snapshot.nodes, snapshot.links);

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

    // Draw links first (below nodes)
    const flowPath = snapshot.traced_paths.length > 0
      ? snapshot.traced_paths[0]!.hops
      : null;
    drawLinks(ctx, layout.links, nodeMap, flowPath, dashOffsetRef.current);

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
      drawNode(ctx, node, isSelected, isIsolated, abrNodes.has(node.id));
    }

    ctx.restore();

    animFrameRef.current = requestAnimationFrame(draw);
  }, [snapshot, selection]);

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
