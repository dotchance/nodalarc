import { useRef, useEffect, useState } from "react";
import * as d3 from "d3";
import type { TopologySnapshot, ConsoleNode } from "../types";
import { computeLayout, computeViewBox, areaColor, CELL_W, CELL_H } from "./layout";
import "../styles/graph.css";

const NODE_R_SAT = 8;    // px radius for satellite nodes
const NODE_R_GS  = 11;   // px radius for ground station nodes

interface Props {
    topology: TopologySnapshot | null;
    selectedNodeId: string | null;
    onNodeSelect: (nodeId: string) => void;
}

export function TopologyGraph({ topology, selectedNodeId, onNodeSelect }: Props) {
    const svgRef = useRef<SVGSVGElement>(null);
    const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

    useEffect(() => {
        if (!svgRef.current) return;
        const observer = new ResizeObserver(entries => {
            const entry = entries[0];
            if (entry) {
                setDimensions({
                    width: entry.contentRect.width,
                    height: entry.contentRect.height,
                });
            }
        });
        observer.observe(svgRef.current.parentElement!);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (!svgRef.current || !topology?.available || !topology.nodes) return;

        const nodes = topology.nodes;
        const links = (topology.links ?? []).filter(l => l.state === "active");

        const nodeMap = computeLayout(nodes);
        computeViewBox(nodeMap);

        const svg = d3.select(svgRef.current);
        svg.selectAll("*").remove();

        // ── Zoom/pan container ───────────────────────────────────────────────
        const g = svg.append("g").attr("class", "graph-root");

        const zoom = d3.zoom<SVGSVGElement, unknown>()
            .scaleExtent([0.3, 4])
            .on("zoom", event => g.attr("transform", event.transform));
        svg.call(zoom);

        // ── Area lane backgrounds ────────────────────────────────────────────
        const areaGroups = new Map<string, { minPlane: number; maxPlane: number }>();
        for (const node of nodes) {
            if (node.node_type !== "satellite" || node.routing_area == null || node.plane == null) continue;
            const existing = areaGroups.get(node.routing_area);
            if (!existing) {
                areaGroups.set(node.routing_area, { minPlane: node.plane, maxPlane: node.plane });
            } else {
                existing.minPlane = Math.min(existing.minPlane, node.plane);
                existing.maxPlane = Math.max(existing.maxPlane, node.plane);
            }
        }

        const maxSlot = nodes.reduce((m, n) => n.slot != null ? Math.max(m, n.slot) : m, 0);
        const laneWidth = 32 + maxSlot * CELL_W + 32;

        areaGroups.forEach((planes, area) => {
            const color = areaColor(area);
            const y0 = 24 + planes.minPlane * CELL_H - 20;
            const y1 = 24 + planes.maxPlane * CELL_H + 20;
            g.append("rect")
                .attr("class", "area-lane")
                .attr("x", 24)
                .attr("y", y0)
                .attr("width", laneWidth)
                .attr("height", y1 - y0)
                .attr("fill", color)
                .attr("fill-opacity", 0.06)
                .attr("stroke", color)
                .attr("stroke-opacity", 0.2)
                .attr("stroke-width", 1)
                .attr("rx", 4);

            g.append("text")
                .attr("class", "area-label")
                .attr("x", 30)
                .attr("y", y0 + 14)
                .attr("fill", color)
                .attr("fill-opacity", 0.7)
                .attr("font-size", 10)
                .text(`Area ${area}`);
        });

        // ── Links ────────────────────────────────────────────────────────────
        g.selectAll(".link")
            .data(links)
            .join("line")
            .attr("class", l => `link link-${l.link_type}`)
            .attr("x1", l => nodeMap.get(l.node_a)?.x ?? 0)
            .attr("y1", l => nodeMap.get(l.node_a)?.y ?? 0)
            .attr("x2", l => nodeMap.get(l.node_b)?.x ?? 0)
            .attr("y2", l => nodeMap.get(l.node_b)?.y ?? 0)
            .attr("stroke", l => {
                if (l.link_type === "ground") return "#00d4aa";
                const na = nodeMap.get(l.node_a);
                const nb = nodeMap.get(l.node_b);
                if (na && nb && na.routing_area !== nb.routing_area) return "rgba(255,255,255,0.4)";
                return areaColor(nodeMap.get(l.node_a)?.routing_area ?? null);
            })
            .attr("stroke-width", l => l.link_type === "ground" ? 2 : 1.5)
            .attr("stroke-dasharray", l => {
                const na = nodeMap.get(l.node_a);
                const nb = nodeMap.get(l.node_b);
                return (na && nb && na.routing_area !== nb.routing_area) ? "4,3" : "none";
            })
            .attr("stroke-opacity", 0.6);

        // ── Nodes ────────────────────────────────────────────────────────────
        const nodeData = Array.from(nodeMap.values());

        // Remove previous tooltip if any
        d3.select(svgRef.current.parentElement!).select(".graph-tooltip").remove();
        const tooltip = d3.select(svgRef.current.parentElement!)
            .append("div")
            .attr("class", "graph-tooltip")
            .style("display", "none");

        function tooltipContent(node: ConsoleNode): string {
            const parts = [`<strong>${node.node_id}</strong>`];
            if (node.routing_area) parts.push(`Area ${node.routing_area}`);
            parts.push(`${node.isl_count} ISL \u00b7 ${node.gnd_count} GND`);
            if (node.prefix) parts.push(`${node.prefix}`);
            return parts.join("<br>");
        }

        const selectedId = selectedNodeId;

        const nodeGroups = g.selectAll<SVGGElement, (typeof nodeData)[0]>(".node")
            .data(nodeData, d => d.node_id)
            .join("g")
            .attr("class", "node")
            .attr("transform", d => `translate(${d.x},${d.y})`)
            .attr("cursor", "pointer")
            .on("click", (_event, d) => onNodeSelect(d.node_id))
            .on("mouseenter", function(_event, d) {
                d3.select(this).select("circle").attr("stroke-width", 2.5);
                tooltip
                    .style("display", "block")
                    .style("left", `${d.x + 16}px`)
                    .style("top", `${d.y - 8}px`)
                    .html(tooltipContent(d));
            })
            .on("mouseleave", function(_event, d) {
                d3.select(this).select("circle").attr("stroke-width", d.node_id === selectedId ? 2.5 : 1.5);
                tooltip.style("display", "none");
            });

        nodeGroups.append("circle")
            .attr("r", d => d.node_type === "satellite" ? NODE_R_SAT : NODE_R_GS)
            .attr("fill", d => areaColor(d.routing_area))
            .attr("fill-opacity", d => d.neighbor_count === 0 ? 0.25 : 0.85)
            .attr("stroke", d => d.node_id === selectedId ? "#ffffff" : areaColor(d.routing_area))
            .attr("stroke-width", d => d.node_id === selectedId ? 2.5 : 1.5);

        // ABR badge: diamond for nodes with links in >1 area
        const abrNodes = new Set<string>();
        for (const lnk of links) {
            const na = nodeMap.get(lnk.node_a);
            const nb = nodeMap.get(lnk.node_b);
            if (na && nb && na.routing_area !== nb.routing_area && na.routing_area !== null && nb.routing_area !== null) {
                abrNodes.add(na.node_id);
                abrNodes.add(nb.node_id);
            }
        }

        nodeGroups.filter(d => abrNodes.has(d.node_id))
            .append("polygon")
            .attr("points", "0,-5 4,0 0,5 -4,0")
            .attr("fill", "#ffffff")
            .attr("fill-opacity", 0.9)
            .attr("transform", `translate(${NODE_R_SAT + 2}, 0)`);

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

    }, [topology, selectedNodeId, onNodeSelect]);

    if (!topology?.available) {
        return (
            <div className="graph-empty">
                <span>Waiting for first topology transition...</span>
            </div>
        );
    }

    return (
        <div className="graph-container">
            <svg
                ref={svgRef}
                width={dimensions.width}
                height={dimensions.height}
                className="topology-svg"
            />
        </div>
    );
}
