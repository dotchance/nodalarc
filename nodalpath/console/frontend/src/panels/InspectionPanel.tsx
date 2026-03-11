import { useInspection } from "../hooks/useInspection";
import type { InspectionNodeResult } from "../types";
import "../styles/inspection.css";

interface Props {
    selectedNodeId: string | null;
    onNodeSelect: (nodeId: string) => void;
}

export function InspectionPanel({ selectedNodeId, onNodeSelect }: Props) {
    const { latestRun, runDetail, triggering, available, triggerInspection, fetchRunDetail } = useInspection();

    if (!available) {
        return (
            <div className="inspection-panel">
                <div className="inspection-header">Inspection</div>
                <div className="inspection-unavailable">
                    Inspection not available (gRPC transport required)
                </div>
            </div>
        );
    }

    const handleInspectAll = () => { triggerInspection(); };
    const handleInspectSelected = () => {
        if (selectedNodeId) triggerInspection([selectedNodeId]);
    };

    const deviatingNodes = runDetail?.node_results?.filter(
        (nr: InspectionNodeResult) => nr.has_deviation || !nr.reachable,
    ) ?? [];

    const nominalCount = runDetail
        ? runDetail.nodes_inspected - deviatingNodes.length
        : 0;

    return (
        <div className="inspection-panel">
            <div className="inspection-header">Inspection</div>

            <div className="inspection-actions">
                <button
                    className="inspection-btn"
                    onClick={handleInspectAll}
                    disabled={triggering}
                >
                    {triggering ? "Inspecting\u2026" : "Interrogate All"}
                </button>
                <button
                    className="inspection-btn inspection-btn--secondary"
                    onClick={handleInspectSelected}
                    disabled={triggering || !selectedNodeId}
                >
                    Interrogate Selected
                </button>
            </div>

            {latestRun && !runDetail && (
                <div className="inspection-run">
                    <div className="inspection-run-meta">
                        <span className="inspection-run-id">{latestRun.run_id}</span>
                        <span className="inspection-trigger">{latestRun.trigger}</span>
                    </div>
                    <div className="inspection-run-stats">
                        {latestRun.nodes_inspected} inspected
                        {latestRun.nodes_with_deviations > 0 && (
                            <span className="inspection-stat--warn">
                                {" \u00b7 "}{latestRun.nodes_with_deviations} deviated
                            </span>
                        )}
                        {latestRun.nodes_unreachable > 0 && (
                            <span className="inspection-stat--error">
                                {" \u00b7 "}{latestRun.nodes_unreachable} unreachable
                            </span>
                        )}
                    </div>
                    <button
                        className="inspection-detail-btn"
                        onClick={() => fetchRunDetail(latestRun.run_id)}
                    >
                        View Details
                    </button>
                </div>
            )}

            {runDetail && (
                <div className="inspection-run">
                    <div className="inspection-run-meta">
                        <span className="inspection-run-id">{runDetail.run_id}</span>
                        <span className="inspection-trigger">{runDetail.trigger}</span>
                    </div>
                    <div className="inspection-run-stats">
                        {runDetail.nodes_inspected} inspected
                        {" \u00b7 "}{nominalCount} nominal
                        {runDetail.nodes_with_deviations > 0 && (
                            <span className="inspection-stat--warn">
                                {" \u00b7 "}{runDetail.nodes_with_deviations} deviated
                            </span>
                        )}
                        {runDetail.nodes_unreachable > 0 && (
                            <span className="inspection-stat--error">
                                {" \u00b7 "}{runDetail.nodes_unreachable} unreachable
                            </span>
                        )}
                    </div>

                    {deviatingNodes.length > 0 && (
                        <div className="inspection-deviations">
                            {deviatingNodes.map((nr: InspectionNodeResult) => (
                                <div
                                    key={nr.node_id}
                                    className={`inspection-node ${nr.has_deviation ? "inspection-node--deviation" : "inspection-node--error"}`}
                                    onClick={() => onNodeSelect(nr.node_id)}
                                >
                                    <div className="inspection-node-id">{nr.node_id}</div>
                                    {!nr.reachable && (
                                        <div className="inspection-diff inspection-diff--error">
                                            Unreachable: {nr.error_message}
                                        </div>
                                    )}
                                    {nr.binding_diffs.map((d, i) => (
                                        <div key={`b-${i}`} className="inspection-diff">
                                            LSR {d.kind}: label {d.in_label}
                                            {d.kind === "mismatch" && (
                                                <span>
                                                    {" "}planned={d.planned_action}/{d.planned_out_label}
                                                    {" "}observed={d.observed_action}/{d.observed_out_label}
                                                </span>
                                            )}
                                        </div>
                                    ))}
                                    {nr.ingress_diffs.map((d, i) => (
                                        <div key={`i-${i}`} className="inspection-diff">
                                            Ingress {d.kind}: {d.dst_prefix}
                                            {d.kind === "mismatch" && (
                                                <span>
                                                    {" "}planned={d.planned_push_label}
                                                    {" "}observed={d.observed_push_label}
                                                </span>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
