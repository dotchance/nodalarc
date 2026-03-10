import { useRef, useEffect, useState } from "react";
import * as d3 from "d3";
import type { TimelineTick, TimelineResponse } from "../types";
import "../styles/timeline.css";

interface Props {
    timeline: TimelineResponse | null;
    selectedSimTime: string | null;
    currentSimTime: string | null;
    onTickSelect: (simTime: string) => void;
    onReturnToLive: () => void;
    windowMinutes: number;
    onWindowChange: (minutes: number) => void;
}

const WINDOW_OPTIONS = [30, 60, 120, 360];

export function TimelinePanel({
    timeline,
    selectedSimTime,
    currentSimTime,
    onTickSelect,
    onReturnToLive,
    windowMinutes,
    onWindowChange,
}: Props) {
    const [collapsed, setCollapsed] = useState(false);
    const svgRef = useRef<SVGSVGElement>(null);

    const lookaheadStatus = timeline?.lookahead_status ?? "disabled";
    const ticks = timeline?.ticks ?? [];

    useEffect(() => {
        if (!svgRef.current || collapsed || !currentSimTime) return;

        const svg = d3.select(svgRef.current);
        svg.selectAll("*").remove();

        const W = svgRef.current.clientWidth || 400;
        const H = 80;
        const margin = { left: 16, right: 16, top: 20, bottom: 24 };
        const innerW = W - margin.left - margin.right;

        const nowMs = new Date(currentSimTime).getTime();
        const halfWindow = (windowMinutes * 60 * 1000) / 2;
        const domainStart = new Date(nowMs - halfWindow);
        const domainEnd = new Date(nowMs + halfWindow);

        const xScale = d3.scaleTime()
            .domain([domainStart, domainEnd])
            .range([0, innerW]);

        const g = svg.append("g")
            .attr("transform", `translate(${margin.left},${margin.top})`);

        // X axis
        g.append("g")
            .attr("transform", `translate(0,${H - margin.top - margin.bottom})`)
            .call(
                d3.axisBottom(xScale)
                    .ticks(6)
                    .tickFormat(d => d3.timeFormat("%H:%M:%S")(d as Date))
            )
            .call(ax => {
                ax.select(".domain").attr("stroke", "#2a2a4e");
                ax.selectAll(".tick line").attr("stroke", "#2a2a4e");
                ax.selectAll(".tick text")
                    .attr("fill", "#888899")
                    .attr("font-size", 9)
                    .attr("font-family", "JetBrains Mono, monospace");
            });

        // Now cursor
        const nowX = xScale(new Date(currentSimTime));
        g.append("line")
            .attr("class", "now-cursor")
            .attr("x1", nowX).attr("x2", nowX)
            .attr("y1", 0).attr("y2", H - margin.top - margin.bottom)
            .attr("stroke", "#00d4aa")
            .attr("stroke-width", 1.5)
            .attr("stroke-dasharray", "4,3");

        g.append("text")
            .attr("x", nowX + 3)
            .attr("y", 10)
            .attr("fill", "#00d4aa")
            .attr("font-size", 8)
            .attr("font-family", "JetBrains Mono, monospace")
            .text("NOW");

        // Transition ticks
        const ticksInView = ticks.filter(t => {
            const ms = new Date(t.sim_time).getTime();
            return ms >= domainStart.getTime() && ms <= domainEnd.getTime();
        });

        const tickHeight = 24;
        const tickY = (H - margin.top - margin.bottom) / 2 - tickHeight / 2;

        // Tick stems
        g.selectAll(".tick-stem")
            .data(ticksInView)
            .join("line")
            .attr("class", "tick-stem")
            .attr("x1", d => xScale(new Date(d.sim_time)))
            .attr("x2", d => xScale(new Date(d.sim_time)))
            .attr("y1", tickY)
            .attr("y2", tickY + tickHeight)
            .attr("stroke", d => tickStemColor(d))
            .attr("stroke-width", d => d.sim_time === selectedSimTime ? 2.5 : 1.5)
            .attr("stroke-dasharray", d => d.is_future ? "3,2" : "none")
            .attr("cursor", "pointer")
            .on("click", (_ev, d) => { if (!d.is_future) onTickSelect(d.sim_time); });

        // Deviation diamonds
        g.selectAll(".dev-diamond")
            .data(ticksInView.filter(d => d.had_deviation))
            .join("polygon")
            .attr("class", "dev-diamond")
            .attr("points", d => {
                const x = xScale(new Date(d.sim_time));
                return `${x},${tickY - 7} ${x + 4},${tickY - 3} ${x},${tickY + 1} ${x - 4},${tickY - 3}`;
            })
            .attr("fill", "#ccaa33")
            .attr("pointer-events", "none");

        // Node count delta labels
        g.selectAll(".delta-label")
            .data(ticksInView.filter(d => d.node_count_delta !== null && d.node_count_delta !== 0))
            .join("text")
            .attr("class", "delta-label")
            .attr("x", d => xScale(new Date(d.sim_time)))
            .attr("y", tickY + tickHeight + 12)
            .attr("text-anchor", "middle")
            .attr("fill", d => (d.node_count_delta ?? 0) > 0 ? "#44cc66" : "#ff3333")
            .attr("font-size", 8)
            .attr("font-family", "JetBrains Mono, monospace")
            .text(d => `${(d.node_count_delta ?? 0) > 0 ? "+" : ""}${d.node_count_delta}`);

        // Selected tick highlight
        if (selectedSimTime) {
            const selX = xScale(new Date(selectedSimTime));
            g.append("circle")
                .attr("cx", selX)
                .attr("cy", tickY + tickHeight / 2)
                .attr("r", 4)
                .attr("fill", "#ffffff")
                .attr("pointer-events", "none");
        }

    }, [timeline, selectedSimTime, currentSimTime, collapsed, windowMinutes]);

    return (
        <div className={`timeline-panel ${collapsed ? "collapsed" : ""}`}>
            <div className="timeline-header">
                <span className="timeline-title">Timeline</span>
                <LookaheadBadge status={lookaheadStatus} />
                <div className="spacer" />
                {!collapsed && (
                    <select
                        className="window-select"
                        value={windowMinutes}
                        onChange={e => onWindowChange(Number(e.target.value))}
                    >
                        {WINDOW_OPTIONS.map(m => (
                            <option key={m} value={m}>&plusmn;{m}m</option>
                        ))}
                    </select>
                )}
                {selectedSimTime && !collapsed && (
                    <button className="return-live-btn" onClick={onReturnToLive}>
                        &#x21A9; Live
                    </button>
                )}
                <button
                    className="collapse-btn"
                    onClick={() => setCollapsed(c => !c)}
                    title={collapsed ? "Expand timeline" : "Collapse timeline"}
                >
                    {collapsed ? "\u25B2" : "\u25BC"}
                </button>
            </div>

            {!collapsed && (
                <div className="timeline-body">
                    {ticks.length === 0
                        ? <div className="timeline-empty">No transitions yet.</div>
                        : <svg ref={svgRef} className="timeline-svg" />
                    }
                </div>
            )}
        </div>
    );
}

function LookaheadBadge({ status }: { status: string }) {
    const config: Record<string, { color: string; label: string }> = {
        disabled:  { color: "#888899", label: "lookahead off" },
        starting:  { color: "#ccaa33", label: "starting\u2026" },
        computing: { color: "#44cc66", label: "computing" },
        waiting:   { color: "#ccaa33", label: "waiting for OME data\u2026" },
        complete:  { color: "#00d4aa", label: "lookahead ready" },
    };
    const entry = config[status] ?? config.disabled;
    const { color, label } = entry!;
    return (
        <span className="lookahead-badge" style={{ color }}>
            {label}
        </span>
    );
}

function tickStemColor(tick: TimelineTick): string {
    if (tick.is_future) return "#ccaa33";
    if (tick.push_succeeded === false) return "#ff3333";
    if (tick.push_succeeded === true) return "#44cc66";
    return "#888899";
}
