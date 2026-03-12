import { useState } from "react";
import { useConsoleState } from "./hooks/useConsoleState";
import { useTopology } from "./hooks/useTopology";
import { useTimeline } from "./hooks/useTimeline";
import { useHistoricalTopology } from "./hooks/useHistoricalTopology";
import { TopBar } from "./bars/TopBar";
import { StatsBar } from "./bars/StatsBar";
import { TopologyGraph } from "./graph/TopologyGraph";
import { NodeDetailPanel } from "./panels/NodeDetailPanel";
import { PathPanel } from "./panels/PathPanel";
import { InspectionPanel } from "./panels/InspectionPanel";
import { TimelinePanel } from "./panels/TimelinePanel";
import { EventLog } from "./panels/EventLog";
import type { PathResult } from "./types";
import { API_BASE } from "./config";
import "./styles/reset.css";
import "./styles/variables.css";
import "./styles/layout.css";

export default function App() {
    const consoleState = useConsoleState();
    const topology = useTopology();
    const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
    const [recomputing, setRecomputing] = useState(false);

    // Historical mode state
    const [selectedSimTime, setSelectedSimTime] = useState<string | null>(null);
    const [windowMinutes, setWindowMinutes] = useState(60);

    // Path overlay state
    const [pathResult, setPathResult] = useState<PathResult | null>(null);

    // Timeline data
    const timeline = useTimeline();

    // Historical topology — fetched when scrubbing, null in live mode
    const historicalTopology = useHistoricalTopology(selectedSimTime);

    // Active topology: historical when scrubbing, live otherwise
    const activeTopology = selectedSimTime ? historicalTopology : topology;

    // Current sim time for the timeline now cursor
    const currentSimTime = consoleState?.last_sim_time ?? null;

    const handleRecompute = async () => {
        setRecomputing(true);
        try {
            await fetch(`${API_BASE}/api/recompute`, { method: "POST" });
        } finally {
            setTimeout(() => setRecomputing(false), 1500);
        }
    };

    const handleTickSelect = (simTime: string) => {
        setSelectedSimTime(simTime);
    };

    const handleReturnToLive = () => {
        setSelectedSimTime(null);
    };

    const topologyNodes = activeTopology?.nodes ?? [];
    const lastPushResult = consoleState?.push_history?.[0] ?? null;
    const sortedNodes = [...topologyNodes].sort((a, b) => a.node_id.localeCompare(b.node_id));

    return (
        <div className="app-root">
            <TopBar
                consoleState={consoleState}
                onRecompute={handleRecompute}
                recomputing={recomputing}
            />
            <StatsBar consoleState={consoleState} />
            <div className="main-area">
                <div className="graph-area">
                    <TopologyGraph
                        topology={activeTopology}
                        selectedNodeId={selectedNodeId}
                        onNodeSelect={setSelectedNodeId}
                        lastPushResult={lastPushResult}
                        pathResult={pathResult}
                    />
                </div>
                <aside className="detail-area">
                    {selectedSimTime && (
                        <div className="historical-banner">
                            <span>&#x23EA; Viewing {selectedSimTime.substring(11, 19)} sim_time</span>
                            <button onClick={handleReturnToLive}>Return to Live</button>
                        </div>
                    )}
                    <NodeDetailPanel
                        nodeId={selectedNodeId}
                        topology_nodes={topologyNodes}
                        consoleState={consoleState}
                        selectedSimTime={selectedSimTime}
                    />
                    <PathPanel
                        nodes={sortedNodes}
                        selectedSimTime={selectedSimTime}
                        onPathResult={setPathResult}
                    />
                    <InspectionPanel
                        selectedNodeId={selectedNodeId}
                        onNodeSelect={setSelectedNodeId}
                    />
                    <TimelinePanel
                        timeline={timeline}
                        selectedSimTime={selectedSimTime}
                        currentSimTime={currentSimTime}
                        onTickSelect={handleTickSelect}
                        onReturnToLive={handleReturnToLive}
                        windowMinutes={windowMinutes}
                        onWindowChange={setWindowMinutes}
                    />
                </aside>
            </div>
            <div className="event-log-area">
                <EventLog events={consoleState?.event_log ?? []} />
            </div>
        </div>
    );
}
