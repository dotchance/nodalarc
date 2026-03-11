import { useRef, useEffect } from "react";
import * as d3 from "d3";
import type { TopologySnapshot, ConsoleNode, PushRecord, PathResult } from "../types";
import { computeLayout, computeViewBox, pushStatusColor } from "./layout";
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

        function nodeStatus(node_id: string): PushStatus {
            if (failedSet.has(node_id)) return "failed";
            if (lastPushResult !== null) return "succeeded";
            return "pending";
        }

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

        // ── Links ────────────────────────────────────────────────────────────
        g.selectAll(".link")
            .data(links)
            .join("line")
            .attr("class", l => `link link-${l.link_type} link-${l.state}`)
            .attr("x1", l => nodeMap.get(l.node_a)?.x ?? 0)
            .attr("y1", l => nodeMap.get(l.node_a)?.y ?? 0)
            .attr("x2", l => nodeMap.get(l.node_b)?.x ?? 0)
            .attr("y2", l => nodeMap.get(l.node_b)?.y ?? 0)
            .attr("stroke", l => {
                if (l.state === "visible_unscheduled") return "rgba(255,255,255,0.2)";
                if (l.link_type === "ground") return "#00d4aa";
                return "rgba(255,255,255,0.35)";
            })
            .attr("stroke-width", l => {
                if (l.state === "visible_unscheduled") return 1;
                return l.link_type === "ground" ? 2 : 1.5;
            })
            .attr("stroke-dasharray", l => {
                if (l.state === "visible_unscheduled") return "2,4";
                return "none";
            })
            .attr("stroke-opacity", l => {
                if (l.state === "visible_unscheduled") return 0.4;
                return 0.5;
            });

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
            .attr("fill", d => pushStatusColor(nodeStatus(d.node_id)))
            .attr("fill-opacity", d => d.neighbor_count === 0 ? 0.25 : 0.85)
            .attr("stroke", d => d.node_id === selectedNodeId
                ? "#ffffff"
                : pushStatusColor(nodeStatus(d.node_id)))
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

            // Draw path lines between consecutive hops
            const hopPairs: Array<[string, string]> = [];
            for (let i = 0; i < hopIds.length - 1; i++) {
                hopPairs.push([hopIds[i]!, hopIds[i + 1]!]);
            }

            g.selectAll(".path-link")
                .data(hopPairs)
                .join("line")
                .attr("class", "path-link")
                .attr("x1", ([a]) => nodeMap.get(a)?.x ?? 0)
                .attr("y1", ([a]) => nodeMap.get(a)?.y ?? 0)
                .attr("x2", ([, b]) => nodeMap.get(b)?.x ?? 0)
                .attr("y2", ([, b]) => nodeMap.get(b)?.y ?? 0)
                .attr("stroke", "#ff8800")
                .attr("stroke-width", 2.5)
                .attr("stroke-opacity", 0.9)
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
