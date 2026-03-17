import { useRef, useEffect } from "react";
import * as d3 from "d3";
import type { TopologySnapshot, ConsoleNode, PushRecord, PathResult } from "../types";
import { computeLayout, computeViewBox, connectivityColor } from "./layout";
import type { PushStatus } from "./layout";
import "../styles/graph.css";

const NODE_R_SAT = 8;    // px radius for satellite nodes
const NODE_R_GS  = 11;   // px radius for ground station nodes

interface Props {
    topology: TopologySnapshot | null;
    selectedNodeId: string | null;
    onNodeSelect: (nodeId: string) => void;
    lastPushResult: PushRecord | null;
    pathResult?: PathResult | null;
}

export function TopologyGraph({ topology, selectedNodeId, onNodeSelect, lastPushResult, pathResult }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const svgRef = useRef<SVGSVGElement>(null);
    const onNodeSelectRef = useRef(onNodeSelect);
    onNodeSelectRef.current = onNodeSelect;

    useEffect(() => {
        if (!svgRef.current || !topology?.available || !topology.nodes) return;

        const nodes = topology.nodes;
        const links = (topology.links ?? []).filter(
            l => l.state === "active" || l.state === "visible_unscheduled"
        );

        const nodeMap = computeLayout(nodes);
        const vb = computeViewBox(nodeMap);

        // Build per-node push status from the most recent push result
        const failedSet = new Set<string>(lastPushResult?.failed_nodes ?? []);

        function _nodeStatus(node_id: string): PushStatus {
            if (failedSet.has(node_id)) return "failed";
            if (lastPushResult !== null) return "succeeded";
            return "pending";
        }
        void _nodeStatus; // Available for future per-node coloring

        const svg = d3.select(svgRef.current);
        svg.selectAll("*").remove();

        // Set viewBox so SVG scales to fit container
        svg.attr("viewBox", `0 0 ${vb.width} ${vb.height}`);

        // ── Zoom/pan container ───────────────────────────────────────────────
        const g = svg.append("g").attr("class", "graph-root");

        const zoom = d3.zoom<SVGSVGElement, unknown>()
            .scaleExtent([0.3, 4])
            .on("zoom", event => g.attr("transform", event.transform));
        svg.call(zoom);

        // ── Wrap detection helper ──────────────────────────────────────────
        // In a ring topology, slot N-1 is adjacent to slot 0 but they
        // appear at opposite ends of the grid row. Detect this so we can
        // draw short stub lines instead of a long cross-grid diagonal.
        const maxSlotAll = Array.from(nodeMap.values())
            .filter(n => n.node_type === "satellite")
            .reduce((m, n) => Math.max(m, n.slot ?? 0), 0);
        const ringSizeAll = maxSlotAll + 1;
        const WRAP_STUB = 20; // px

        function isRingWrap(aId: string, bId: string): boolean {
            const a = nodeMap.get(aId);
            const b = nodeMap.get(bId);
            if (!a || !b) return false;
            if (a.node_type !== "satellite" || b.node_type !== "satellite") return false;
            if (a.plane !== b.plane || a.plane == null) return false;
            return Math.abs((a.slot ?? 0) - (b.slot ?? 0)) > ringSizeAll / 2;
        }

        // ── Links ────────────────────────────────────────────────────────────
        // For ring-wrap links, draw two stubs instead of one long line.
        interface LinkSegment {
            cls: string; stroke: string; strokeW: number;
            dash: string; opacity: number;
            x1: number; y1: number; x2: number; y2: number;
        }
        const linkSegments: LinkSegment[] = [];

        for (const l of links) {
            const a = nodeMap.get(l.node_a);
            const b = nodeMap.get(l.node_b);
            if (!a || !b) continue;

            const cls = `link link-${l.link_type} link-${l.state}`;
            const stroke = l.state === "visible_unscheduled"
                ? "rgba(255,255,255,0.2)"
                : l.link_type === "ground" ? "#00d4aa" : "rgba(255,255,255,0.35)";
            const strokeW = l.state === "visible_unscheduled" ? 1 : l.link_type === "ground" ? 2 : 1.5;
            const dash = l.state === "visible_unscheduled" ? "2,4" : "none";
            const opacity = l.state === "visible_unscheduled" ? 0.4 : 0.5;

            if (isRingWrap(l.node_a, l.node_b)) {
                const aRight = (a.slot ?? 0) > (b.slot ?? 0);
                linkSegments.push({
                    cls, stroke, strokeW, dash, opacity,
                    x1: a.x, y1: a.y,
                    x2: a.x + (aRight ? WRAP_STUB : -WRAP_STUB), y2: a.y,
                });
                linkSegments.push({
                    cls, stroke, strokeW, dash, opacity,
                    x1: b.x - (aRight ? WRAP_STUB : -WRAP_STUB), y1: b.y,
                    x2: b.x, y2: b.y,
                });
            } else {
                linkSegments.push({
                    cls, stroke, strokeW, dash, opacity,
                    x1: a.x, y1: a.y, x2: b.x, y2: b.y,
                });
            }
        }

        g.selectAll(".link")
            .data(linkSegments)
            .join("line")
            .attr("class", d => d.cls)
            .attr("x1", d => d.x1)
            .attr("y1", d => d.y1)
            .attr("x2", d => d.x2)
            .attr("y2", d => d.y2)
            .attr("stroke", d => d.stroke)
            .attr("stroke-width", d => d.strokeW)
            .attr("stroke-dasharray", d => d.dash)
            .attr("stroke-opacity", d => d.opacity);

        // ── Nodes ────────────────────────────────────────────────────────────
        const nodeData = Array.from(nodeMap.values());

        // Remove previous tooltip if any
        if (containerRef.current) {
            d3.select(containerRef.current).select(".graph-tooltip").remove();
        }
        const tooltip = containerRef.current
            ? d3.select(containerRef.current)
                .append("div")
                .attr("class", "graph-tooltip")
                .style("display", "none")
            : null;

        function tooltipContent(node: ConsoleNode): string {
            const parts = [`<strong>${node.node_id}</strong>`];
            if (node.routing_area) parts.push(`Area ${node.routing_area}`);

            const nodeLinks = links.filter(
                l => l.node_a === node.node_id || l.node_b === node.node_id
            );
            const activeCount = nodeLinks.filter(l => l.state === "active").length;
            const visUnscheduled = nodeLinks.filter(l => l.state === "visible_unscheduled").length;

            if (visUnscheduled > 0) {
                parts.push(`${activeCount} active \u00b7 ${visUnscheduled} vis/unscheduled`);
            } else {
                parts.push(`${node.isl_count} ISL \u00b7 ${node.gnd_count} GND`);
            }

            if (node.prefix) parts.push(`${node.prefix}`);
            return parts.join("<br>");
        }

        const nodeGroups = g.selectAll<SVGGElement, (typeof nodeData)[0]>(".node")
            .data(nodeData, d => d.node_id)
            .join("g")
            .attr("class", "node")
            .attr("transform", d => `translate(${d.x},${d.y})`)
            .attr("cursor", "pointer")
            .on("click", (_event, d) => onNodeSelectRef.current(d.node_id))
            .on("mouseenter", function(_event, d) {
                d3.select(this).select("circle").attr("stroke-width", 2.5);
                tooltip
                    ?.style("display", "block")
                    .style("left", `${d.x + 16}px`)
                    .style("top", `${d.y - 8}px`)
                    .html(tooltipContent(d));
            })
            .on("mouseleave", function(_event, d) {
                d3.select(this).select("circle").attr("stroke-width", d.node_id === selectedNodeId ? 2.5 : 1.5);
                tooltip?.style("display", "none");
            });

        nodeGroups.append("circle")
            .attr("r", d => d.node_type === "satellite" ? NODE_R_SAT : NODE_R_GS)
            .attr("fill", d => connectivityColor(d))
            .attr("fill-opacity", d => d.neighbor_count === 0 ? 0.25 : 0.85)
            .attr("stroke", d => d.node_id === selectedNodeId
                ? "#ffffff"
                : connectivityColor(d))
            .attr("stroke-width", d => d.node_id === selectedNodeId ? 2.5 : 1.5);

        // Node labels
        nodeGroups.append("text")
            .attr("class", "node-label")
            .attr("y", d => (d.node_type === "satellite" ? NODE_R_SAT : NODE_R_GS) + 13)
            .attr("text-anchor", "middle")
            .attr("fill", "#888899")
            .attr("font-size", 9)
            .text(d => d.node_type === "satellite"
                ? `P${String(d.plane).padStart(2,"0")}S${String(d.slot).padStart(2,"0")}`
                : d.node_id.replace(/^gs-/, ""));

        // ── Path overlay ─────────────────────────────────────────────────────
        if (pathResult?.reachable && pathResult.hops.length > 0) {
            const hopIds = pathResult.hops.map(h => h.node_id);

            // Highlight hop nodes
            nodeGroups
                .filter(d => hopIds.includes(d.node_id))
                .select("circle")
                .attr("stroke", "#ff8800")
                .attr("stroke-width", 3);

            // Draw path lines between consecutive hops.
            // Uses the same ring-wrap detection as topology links.
            interface PathSeg { x1: number; y1: number; x2: number; y2: number; }
            const pathSegs: PathSeg[] = [];

            for (let i = 0; i < hopIds.length - 1; i++) {
                const a = nodeMap.get(hopIds[i]!);
                const b = nodeMap.get(hopIds[i + 1]!);
                if (!a || !b) continue;

                if (isRingWrap(hopIds[i]!, hopIds[i + 1]!)) {
                    const aRight = (a.slot ?? 0) > (b.slot ?? 0);
                    pathSegs.push({
                        x1: a.x, y1: a.y,
                        x2: a.x + (aRight ? WRAP_STUB : -WRAP_STUB), y2: a.y,
                    });
                    pathSegs.push({
                        x1: b.x - (aRight ? WRAP_STUB : -WRAP_STUB), y1: b.y,
                        x2: b.x, y2: b.y,
                    });
                } else {
                    pathSegs.push({ x1: a.x, y1: a.y, x2: b.x, y2: b.y });
                }
            }

            g.selectAll(".path-link")
                .data(pathSegs)
                .join("line")
                .attr("class", "path-link")
                .attr("x1", d => d.x1)
                .attr("y1", d => d.y1)
                .attr("x2", d => d.x2)
                .attr("y2", d => d.y2)
                .attr("stroke", "#ff8800")
                .attr("stroke-width", 2.5)
                .attr("stroke-opacity", 0.9)
                .attr("stroke-dasharray", "6 3")
                .attr("pointer-events", "none");

            // Hop index labels on nodes
            g.selectAll(".hop-index")
                .data(pathResult.hops)
                .join("text")
                .attr("class", "hop-index")
                .attr("x", h => (nodeMap.get(h.node_id)?.x ?? 0) + 12)
                .attr("y", h => (nodeMap.get(h.node_id)?.y ?? 0) - 10)
                .attr("fill", "#ff8800")
                .attr("font-size", 9)
                .attr("font-family", "JetBrains Mono, monospace")
                .attr("pointer-events", "none")
                .text((_, i) => String(i + 1));
        }

    }, [topology, selectedNodeId, lastPushResult, pathResult]);

    if (!topology?.available) {
        return (
            <div className="graph-empty">
                <span>Waiting for first topology transition...</span>
            </div>
        );
    }

    const isHistorical = topology && 'is_historical' in topology && (topology as any).links_available;

    return (
        <div ref={containerRef} className="graph-container">
            <svg
                ref={svgRef}
                className="topology-svg"
            />
            {isHistorical && (
                <div className="graph-legend">
                    <span className="legend-item legend-active">{"\u2500"} active</span>
                    <span className="legend-item legend-vis-unscheduled">{"\u254C"} vis/unscheduled</span>
                </div>
            )}
        </div>
    );
}
