import { useState, useEffect } from "react";
import type { ConsoleNode, ConsoleStateSnapshot, NodeStateDetail } from "../types";
import { API_BASE, getGlobeBase } from "../config";
import "../styles/panels.css";

type Tab = "state" | "forwarding" | "history";

interface Props {
    nodeId: string | null;
    topology_nodes: ConsoleNode[];
    consoleState: ConsoleStateSnapshot | null;
    selectedSimTime?: string | null;
}

export function NodeDetailPanel({ nodeId, topology_nodes, consoleState, selectedSimTime }: Props) {
    const [tab, setTab] = useState<Tab>("state");
    const [nodeDetail, setNodeDetail] = useState<NodeStateDetail | null>(null);
    const [loadingDetail, setLoadingDetail] = useState(false);

    const node = topology_nodes.find(n => n.node_id === nodeId) ?? null;

    // Fetch forwarding detail when node changes or forwarding tab is opened
    useEffect(() => {
        if (!nodeId || tab !== "forwarding") return;
        let cancelled = false;
        setLoadingDetail(true);
        const url = selectedSimTime
            ? `${API_BASE}/api/v1/node/${encodeURIComponent(nodeId)}/state/at/${encodeURIComponent(selectedSimTime)}`
            : `${API_BASE}/api/v1/node/${encodeURIComponent(nodeId)}/state`;
        fetch(url)
            .then(r => r.json())
            .then(data => { if (!cancelled) setNodeDetail(data); })
            .catch(() => { if (!cancelled) setNodeDetail({ available: false, reason: "fetch error" }); })
            .finally(() => { if (!cancelled) setLoadingDetail(false); });
        return () => { cancelled = true; };
    }, [nodeId, tab, selectedSimTime]);

    // Reset tab when node changes
    useEffect(() => { setTab("state"); setNodeDetail(null); }, [nodeId]);

    if (!nodeId || !node) {
        return (
            <div className="detail-panel detail-panel--empty">
                <p>Select a node in the graph to inspect it.</p>
            </div>
        );
    }

    // Filter push history for this node
    const relevantPushes = (consoleState?.push_history ?? [])
        .filter(p => p.failed_nodes.includes(nodeId) || p.nodes_attempted > 0)
        .slice(0, 10);

    // Filter deviations involving this node
    const relevantDeviations = (consoleState?.deviation_history ?? [])
        .filter(d => d.node_a === nodeId || d.node_b === nodeId)
        .slice(0, 5);

    const globeUrl = `${getGlobeBase()}/?selected=${encodeURIComponent(nodeId)}`;

    return (
        <div className="detail-panel">
            {/* ── Historical indicator ── */}
            {selectedSimTime && (
                <div className="historical-indicator">
                    Historical @ {selectedSimTime.substring(11, 19)}
                </div>
            )}

            {/* ── Header ── */}
            <div className="detail-header">
                <div className="detail-node-id">{nodeId}</div>
                <div className="detail-node-meta">
                    {node.node_type === "satellite"
                        ? `Plane ${node.plane} \u00b7 Slot ${node.slot} \u00b7 Area ${node.routing_area ?? "\u2014"}`
                        : `Ground Station \u00b7 ${node.prefix ?? "no prefix"}`
                    }
                </div>
                <a
                    className="globe-link"
                    href={globeUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="View in NodalArc globe"
                >
                    \u2197 Globe
                </a>
            </div>

            {/* ── Tabs ── */}
            <div className="detail-tabs">
                {(["state", "forwarding", "history"] as Tab[]).map(t => (
                    <button
                        key={t}
                        className={`detail-tab ${tab === t ? "active" : ""}`}
                        onClick={() => setTab(t)}
                    >
                        {t.charAt(0).toUpperCase() + t.slice(1)}
                    </button>
                ))}
            </div>

            {/* ── Tab: State ── */}
            {tab === "state" && (
                <div className="detail-body">
                    <table className="kv-table">
                        <tbody>
                            <tr><td>Type</td><td>{node.node_type}</td></tr>
                            <tr><td>Routing Area</td><td>{node.routing_area ?? "\u2014"}</td></tr>
                            <tr><td>ISL Adjacencies</td><td>{node.isl_count}</td></tr>
                            <tr><td>Ground Links</td><td>{node.gnd_count}</td></tr>
                            <tr><td>Total Neighbors</td><td>{node.neighbor_count}</td></tr>
                            {node.prefix && <tr><td>Prefix</td><td>{node.prefix}</td></tr>}
                        </tbody>
                    </table>
                    {relevantDeviations.length > 0 && (
                        <div className="deviation-alert">
                            <div className="deviation-alert-label">Recent Deviations</div>
                            {relevantDeviations.map((d, i) => (
                                <div key={i} className="deviation-row">
                                    <span className="dev-reason">{d.reason}</span>
                                    <span className="dev-time">{d.sim_time.substring(11, 19)}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* ── Tab: Forwarding ── */}
            {tab === "forwarding" && (
                <div className="detail-body">
                    {loadingDetail && <div className="loading">Loading...</div>}
                    {!loadingDetail && !nodeDetail?.available && (
                        <div className="unavailable">
                            {nodeDetail?.reason ?? "Forwarding data unavailable"}
                        </div>
                    )}
                    {!loadingDetail && nodeDetail?.available && (
                        <>
                            <div className="forwarding-meta">
                                State: {nodeDetail.topology_state_id?.slice(0, 12)}...
                            </div>
                            {(nodeDetail.forwarding_entries ?? []).length === 0 ? (
                                <div className="unavailable">No forwarding entries.</div>
                            ) : (
                                <table className="fwd-table">
                                    <thead>
                                        <tr>
                                            <th>Dst</th>
                                            <th>Next Hop</th>
                                            <th>Op</th>
                                            <th>In/Out Label</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {(nodeDetail.forwarding_entries ?? []).map((e, i) => (
                                            <tr key={i}>
                                                <td>{e.destination}</td>
                                                <td>{e.next_hop}</td>
                                                <td className={`op-${e.operation}`}>{e.operation?.toUpperCase()}</td>
                                                <td>{e.incoming_label ?? "\u2014"} / {e.outgoing_label ?? "\u2014"}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </>
                    )}
                </div>
            )}

            {/* ── Tab: History ── */}
            {tab === "history" && (
                <div className="detail-body">
                    <div className="history-section-label">Recent Pushes (all nodes)</div>
                    {relevantPushes.length === 0
                        ? <div className="unavailable">No push history.</div>
                        : relevantPushes.map((p, i) => (
                            <div key={i} className={`push-row ${p.nodes_failed > 0 ? "push-failed" : "push-ok"}`}>
                                <span className="push-ratio">{p.nodes_succeeded}/{p.nodes_attempted}</span>
                                <span className="push-time">{p.push_duration_ms.toFixed(0)}ms</span>
                                <span className="push-simtime">{p.sim_time.substring(11, 19)}</span>
                                {p.failed_nodes.includes(nodeId) && (
                                    <span className="push-failed-badge">FAILED</span>
                                )}
                            </div>
                        ))
                    }
                </div>
            )}
        </div>
    );
}
