import { useState, useEffect } from "react";
import type { ConsoleNode, PathResult } from "../types";
import { usePath } from "../hooks/usePath";
import "../styles/path.css";

interface Props {
    groundStations: ConsoleNode[];
    selectedSimTime: string | null;
    onPathResult: (result: PathResult | null) => void;
}

export function PathPanel({ groundStations, selectedSimTime, onPathResult }: Props) {
    const [src, setSrc] = useState<string>("");
    const [dst, setDst] = useState<string>("");
    const { result, loading, error, query, clear } = usePath();

    // Notify parent of path result changes (for overlay rendering in graph)
    useEffect(() => { onPathResult(result); }, [result]);

    // Re-query when sim_time changes (historical mode scrub)
    useEffect(() => {
        if (src && dst && src !== dst) {
            query(src, dst, selectedSimTime);
        }
    }, [selectedSimTime]);

    const handleQuery = () => {
        if (src && dst && src !== dst) {
            query(src, dst, selectedSimTime);
        }
    };

    const handleClear = () => {
        clear();
        onPathResult(null);
    };

    const gsIds = groundStations.map(n => n.node_id);

    return (
        <div className="path-panel">
            <div className="path-panel-header">Path Overlay</div>

            {/* Source / Destination selectors */}
            <div className="path-selectors">
                <div className="path-selector-row">
                    <label className="path-label">From</label>
                    <select
                        className="path-select"
                        value={src}
                        onChange={e => setSrc(e.target.value)}
                    >
                        <option value="">— ground station —</option>
                        {gsIds.map(id => (
                            <option key={id} value={id}>{id.replace(/^gs-/, "")}</option>
                        ))}
                    </select>
                </div>
                <div className="path-selector-row">
                    <label className="path-label">To</label>
                    <select
                        className="path-select"
                        value={dst}
                        onChange={e => setDst(e.target.value)}
                    >
                        <option value="">— ground station —</option>
                        {gsIds.filter(id => id !== src).map(id => (
                            <option key={id} value={id}>{id.replace(/^gs-/, "")}</option>
                        ))}
                    </select>
                </div>
                <div className="path-actions">
                    <button
                        className="path-query-btn"
                        onClick={handleQuery}
                        disabled={!src || !dst || src === dst || loading}
                    >
                        {loading ? "Deriving\u2026" : "Derive Path"}
                    </button>
                    {result && (
                        <button className="path-clear-btn" onClick={handleClear}>
                            Clear
                        </button>
                    )}
                </div>
            </div>

            {/* Error */}
            {error && <div className="path-error">{error}</div>}

            {/* Unreachable */}
            {result && !result.reachable && (
                <div className="path-unreachable">
                    <span className="path-unreach-icon">{"\u2715"}</span>
                    No path: {result.unreachable_reason}
                </div>
            )}

            {/* Hop list + latency */}
            {result?.reachable && (
                <div className="path-result">
                    <div className="path-summary">
                        {result.hops.length} hops {"\u00b7"} {result.total_latency_ms.toFixed(1)} ms total
                        <span className="path-method">{result.method}</span>
                    </div>
                    <div className="path-hops">
                        {result.hops.map((hop, i) => (
                            <div key={i} className={`path-hop path-hop-${hop.node_type}`}>
                                <div className="hop-node-id">
                                    {hop.node_type === "ground_station"
                                        ? hop.node_id.replace(/^gs-/, "GS: ")
                                        : hop.node_id}
                                </div>
                                <div className="hop-mpls">
                                    {hop.action && (
                                        <span className={`hop-action hop-action-${hop.action}`}>
                                            {hop.action.toUpperCase()}
                                        </span>
                                    )}
                                    {hop.in_label != null && (
                                        <span className="hop-label">
                                            {hop.in_label}
                                            {hop.out_label != null && ` \u2192 ${hop.out_label}`}
                                        </span>
                                    )}
                                    {hop.out_interface && (
                                        <span className="hop-iface">{hop.out_interface}</span>
                                    )}
                                </div>
                                {hop.latency_to_next_ms != null && (
                                    <div className="hop-latency">
                                        {"\u2193"} {hop.latency_to_next_ms.toFixed(1)} ms
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
