import { useState, useEffect, useRef, useCallback } from "react";
import type { ConsoleNode, PathResult, TraceConfig, LiveTraceStatus, LiveTraceDirection } from "../types";
import { usePath } from "../hooks/usePath";
import { API_BASE } from "../config";
import "../styles/path.css";

interface Props {
    nodes: ConsoleNode[];
    selectedSimTime: string | null;
    onPathResult: (result: PathResult | null) => void;
    traceConfig: TraceConfig | null;
}

const METHOD_LABELS: Record<string, string> = {
    cspf: "CSPF", derived: "Derived", traceroute: "Traceroute",
    "traceroute-sr": "SR Traceroute", "traceroute-sr-pipe": "SR Pipe",
    tracepath: "Tracepath", "tracepath-sr": "SR Tracepath",
    "tracepath-sr-pipe": "SR Pipe", probed: "Traceroute", none: "\u2014",
};

export function PathPanel({ nodes, selectedSimTime, onPathResult, traceConfig }: Props) {
    const [src, setSrc] = useState<string>("");
    const [dst, setDst] = useState<string>("");
    const [showRaw, setShowRaw] = useState(false);
    const { result, loading, error, query, clear } = usePath();

    const [liveTrace, setLiveTrace] = useState<LiveTraceStatus | null>(null);
    const [liveLoading, setLiveLoading] = useState(false);
    const [liveError, setLiveError] = useState<string | null>(null);
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const [countdown, setCountdown] = useState<string | null>(null);
    const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

    useEffect(() => { onPathResult(result); }, [result]);

    useEffect(() => {
        if (src && dst && src !== dst) query(src, dst, selectedSimTime);
    }, [selectedSimTime]);

    const isLiveActive = liveTrace?.active ?? false;
    const liveResult = liveTrace?.result ?? null;

    // Poll trace status
    useEffect(() => {
        if (!isLiveActive) return;
        const poll = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/v1/trace/status`);
                const data: LiveTraceStatus = await r.json();
                setLiveTrace(data);
                if (!data.active && pollRef.current) {
                    clearInterval(pollRef.current);
                    pollRef.current = null;
                }
            } catch { /* ignore */ }
        };
        poll();
        pollRef.current = setInterval(poll, 2000);
        return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
    }, [isLiveActive && liveTrace?.src && liveTrace?.dst]);

    // Countdown from path_valid_seconds (sim time delta from when trace was taken)
    useEffect(() => {
        if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }

        const secs = liveTrace?.result?.path_valid_seconds;
        const tracedAt = liveTrace?.result?.traced_at;
        if (secs == null || secs <= 0 || !tracedAt) {
            setCountdown(secs != null && secs <= 0 ? "Path change expected" : null);
            return;
        }
        const expiresAt = new Date(tracedAt).getTime() + secs * 1000;
        const tick = () => {
            const remaining = (expiresAt - Date.now()) / 1000;
            if (remaining <= 0) {
                setCountdown("Path change expected");
            } else {
                const m = Math.floor(remaining / 60);
                const s = Math.floor(remaining % 60);
                setCountdown(m > 0 ? `${m}m ${s}s` : `${s}s`);
            }
        };
        tick();
        countdownRef.current = setInterval(tick, 1000);
        return () => { if (countdownRef.current) clearInterval(countdownRef.current); };
    }, [liveTrace?.result?.path_valid_seconds, liveTrace?.result?.traced_at]);

    const handleQuery = () => {
        if (src && dst && src !== dst) { setShowRaw(false); query(src, dst, selectedSimTime); }
    };
    const handleClear = () => { clear(); setShowRaw(false); onPathResult(null); };

    const handleStartLiveTrace = useCallback(async () => {
        if (!src || !dst || src === dst) return;
        setLiveLoading(true); setLiveError(null);
        try {
            const r = await fetch(`${API_BASE}/api/v1/trace/start`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ src_node: src, dst_node: dst }),
            });
            const data = await r.json();
            if (r.ok && data.ok) { setLiveTrace({ active: true, src, dst, result: null }); }
            else { setLiveError(data.error ?? data.detail ?? `Failed (${r.status})`); }
        } catch { setLiveError("Failed to start live trace"); }
        finally { setLiveLoading(false); }
    }, [src, dst]);

    const handleStopLiveTrace = useCallback(async () => {
        try { await fetch(`${API_BASE}/api/v1/trace/stop`, { method: "POST" }); } catch {}
        setLiveTrace(null); setCountdown(null);
    }, []);

    const nodeIds = nodes.map(n => n.node_id);
    const isCspf = traceConfig?.trace_mode === "cspf";
    const buttonLabel = isCspf ? "Derive Path" : "Trace Path";

    return (
        <div className="path-panel">
            <div className="path-panel-header">Path Overlay</div>

            <div className="path-selectors">
                <div className="path-selector-row">
                    <label className="path-label">From</label>
                    <select className="path-select" value={src} onChange={e => setSrc(e.target.value)}>
                        <option value="">— select —</option>
                        {nodeIds.map(id => <option key={id} value={id}>{id}</option>)}
                    </select>
                </div>
                <div className="path-selector-row">
                    <label className="path-label">To</label>
                    <select className="path-select" value={dst} onChange={e => setDst(e.target.value)}>
                        <option value="">— select —</option>
                        {nodeIds.filter(id => id !== src).map(id => <option key={id} value={id}>{id}</option>)}
                    </select>
                </div>
                <div className="path-actions">
                    <button className="path-query-btn" onClick={handleQuery}
                        disabled={!src || !dst || src === dst || loading}>
                        {loading ? "Tracing\u2026" : buttonLabel}
                    </button>
                    {!isLiveActive ? (
                        <button className="path-live-btn" onClick={handleStartLiveTrace}
                            disabled={!src || !dst || src === dst || liveLoading}>
                            {liveLoading ? "Starting\u2026" : "Trace Live"}
                        </button>
                    ) : (
                        <button className="path-stop-btn" onClick={handleStopLiveTrace}>Stop</button>
                    )}
                    {result && !isLiveActive && (
                        <button className="path-clear-btn" onClick={handleClear}>Clear</button>
                    )}
                </div>
            </div>

            {error && <div className="path-error">{error}</div>}
            {liveError && <div className="path-error">{liveError}</div>}

            {/* Live trace results */}
            {isLiveActive && liveResult && (
                <div className="path-result">
                    <div className="path-summary">
                        <span className="path-live-indicator">LIVE</span>
                        {liveResult.forward.hops.length} hops
                        {" \u00b7 "}{liveResult.forward.rtt_ms.toFixed(1)} ms fwd
                        {" / "}{liveResult.reverse.rtt_ms.toFixed(1)} ms rev
                        <span className="path-method">
                            {METHOD_LABELS[liveResult.method] ?? liveResult.method}
                        </span>
                    </div>

                    {(liveResult.forward.asymmetry_detected || liveResult.reverse.asymmetry_detected) && (
                        <div className="path-asymmetry-warning">
                            Path asymmetry detected — forward and reverse paths differ
                        </div>
                    )}

                    {countdown !== null && (
                        <div className="path-validity-countdown">{countdown}</div>
                    )}

                    <div className="path-dual-columns">
                        <div className="path-column">
                            <div className="path-direction-label">Forward</div>
                            <DirectionHops direction={liveResult.forward} />
                        </div>
                        <div className="path-column">
                            <div className="path-direction-label">Reverse</div>
                            <DirectionHops direction={liveResult.reverse} />
                        </div>
                    </div>
                </div>
            )}

            {isLiveActive && !liveResult && (
                <div className="path-summary" style={{ padding: "8px 12px", color: "var(--text-sec)" }}>
                    Tracing path...
                </div>
            )}

            {/* One-shot results (unchanged) */}
            {!isLiveActive && result?.pipe_mode && (
                <div className="path-pipe-warning">MPLS pipe mode — intermediate hops hidden</div>
            )}
            {!isLiveActive && result && !result.reachable && (
                <div className="path-unreachable">
                    <span className="path-unreach-icon">{"\u2715"}</span>
                    <span>{result.unreachable_reason}</span>
                    {result.raw_output && (
                        <button className="path-raw-toggle" onClick={() => setShowRaw(v => !v)}>
                            {showRaw ? "hide raw" : "raw output"}
                        </button>
                    )}
                </div>
            )}
            {!isLiveActive && showRaw && result?.raw_output && (
                <pre className="path-raw-output">{result.raw_output}</pre>
            )}
            {!isLiveActive && result && result.hops.length > 0 && (
                <div className="path-result">
                    <div className="path-summary">
                        {result.hops.length} hops
                        {result.reachable
                            ? <>{" \u00b7 "}{result.total_latency_ms.toFixed(1)} ms</>
                            : <>{" \u00b7 "}<span className="path-partial-label">partial</span></>}
                        <span className="path-method">{METHOD_LABELS[result.method] ?? result.method}</span>
                    </div>
                    <div className="path-hops">
                        {result.hops.map((hop, i) => (
                            <div key={i} className={`path-hop path-hop-${hop.node_type}`}>
                                <div className="hop-node-id">
                                    {hop.node_type === "ground_station" ? hop.node_id.replace(/^gs-/, "GS: ") : hop.node_id}
                                </div>
                                <div className="hop-mpls">
                                    {hop.action && <span className={`hop-action hop-action-${hop.action}`}>{hop.action.toUpperCase()}</span>}
                                    {hop.in_label != null && <span className="hop-label">{hop.in_label}{hop.out_label != null && ` \u2192 ${hop.out_label}`}</span>}
                                    {hop.out_interface && <span className="hop-iface">{hop.out_interface}</span>}
                                </div>
                                {hop.latency_to_next_ms != null && (
                                    <div className="hop-latency">{"\u2193"} {hop.latency_to_next_ms.toFixed(1)} ms</div>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

/** Render hops with per-hop latency (delta between consecutive RTTs) and netem delays. */
function DirectionHops({ direction }: { direction: LiveTraceDirection }) {
    return (
        <div className="path-hops">
            {direction.hops.map((hop, i) => {
                const link = i < direction.links.length ? direction.links[i] : null;
                // Per-hop latency = delta between this hop's RTT and previous hop's RTT
                const prevRtt = i > 0 ? (direction.hops[i - 1]?.rtt_ms ?? 0) : 0;
                const thisRtt = hop.rtt_ms ?? 0;
                const hopLatency = i > 0 && thisRtt > 0 ? thisRtt - prevRtt : null;

                return (
                    <div key={i} className={`path-hop path-hop-${hop.node_type}`}>
                        <div className="hop-node-id">
                            {hop.node_type === "ground_station"
                                ? hop.node_id.replace(/^gs-/, "GS: ")
                                : hop.node_id}
                            {thisRtt > 0 && (
                                <span className="hop-rtt">{thisRtt.toFixed(1)} ms</span>
                            )}
                        </div>
                        {(hopLatency != null || (link && link.netem_delay_ms != null)) && (
                            <div className="hop-latency">
                                {hopLatency != null && (
                                    <span>{"\u2193"} {hopLatency.toFixed(1)} ms</span>
                                )}
                                {link && link.netem_delay_ms != null && (
                                    <span className="hop-netem">
                                        netem {link.netem_delay_ms.toFixed(1)} ms
                                    </span>
                                )}
                                {link && link.interface && (
                                    <span className="hop-iface">{link.interface}</span>
                                )}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
}
