/** TopologyView — HTML5 Canvas 2D topology diagram. */

import { useEffect, useRef, useCallback } from "react";
import { computeLayout } from "./layout";
import { drawNode, hitTestNode } from "./nodes";
import { drawLinks } from "./topoLinks";
import { setupInteraction, type ViewTransform } from "./interaction";
import type { StateSnapshot, Selection } from "../types";

interface TopologyViewProps {
  snapshot: StateSnapshot | null;
  selection: Selection | null;
  onSelect: (sel: Selection | null) => void;
}

export function TopologyView({ snapshot, selection, onSelect }: TopologyViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const transformRef = useRef<ViewTransform>({ offsetX: 0, offsetY: 0, scale: 1 });
  const animFrameRef = useRef<number>(0);

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
    const t = transformRef.current;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.translate(t.offsetX, t.offsetY);
    ctx.scale(t.scale, t.scale);

    // Build node map for link drawing
    const nodeMap = new Map(layout.nodes.map((n) => [n.id, n]));

    // Draw links first (below nodes)
    const flowPath = snapshot.traced_paths.length > 0
      ? snapshot.traced_paths[0]!.hops
      : null;
    drawLinks(ctx, layout.links, nodeMap, flowPath);

    // Draw nodes
    // Determine isolated nodes (no active links)
    const connectedNodes = new Set<string>();
    for (const l of layout.links) {
      connectedNodes.add(l.nodeA);
      connectedNodes.add(l.nodeB);
    }

    for (const node of layout.nodes) {
      const isSelected = selection?.id === node.id;
      const isIsolated = !connectedNodes.has(node.id);
      drawNode(ctx, node, isSelected, isIsolated);
    }

    ctx.restore();

    animFrameRef.current = requestAnimationFrame(draw);
  }, [snapshot, selection]);

  useEffect(() => {
    animFrameRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [draw]);

  // Setup pan/zoom interaction
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
        const hit = hitTestNode(worldX, worldY, layout.nodes);
        if (hit) {
          onSelect({
            type: hit.type === "ground_station" ? "ground_station" : "satellite",
            id: hit.id,
          });
        } else {
          onSelect(null);
        }
      },
    );

    return cleanup;
  }, [snapshot, onSelect]);

  return (
    <canvas
      ref={canvasRef}
      className="topology-view"
    />
  );
}
