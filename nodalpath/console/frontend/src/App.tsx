import { useState } from "react";
import { useConsoleState } from "./hooks/useConsoleState";
import { useTopology } from "./hooks/useTopology";
import { TopBar } from "./bars/TopBar";
import { StatsBar } from "./bars/StatsBar";
import { TopologyGraph } from "./graph/TopologyGraph";
import { NodeDetailPanel } from "./panels/NodeDetailPanel";
import { EventLog } from "./panels/EventLog";
import { API_BASE } from "./config";
import "./styles/reset.css";
import "./styles/variables.css";
import "./styles/layout.css";

export default function App() {
    const consoleState = useConsoleState();
    const topology = useTopology();
    const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
    const [recomputing, setRecomputing] = useState(false);

    const handleRecompute = async () => {
        setRecomputing(true);
        try {
            await fetch(`${API_BASE}/api/recompute`, { method: "POST" });
        } finally {
            setTimeout(() => setRecomputing(false), 1500);
        }
    };

    const topologyNodes = topology?.nodes ?? [];

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
                        topology={topology}
                        selectedNodeId={selectedNodeId}
                        onNodeSelect={setSelectedNodeId}
                    />
                </div>
                <aside className="detail-area">
                    <NodeDetailPanel
                        nodeId={selectedNodeId}
                        topology_nodes={topologyNodes}
                        consoleState={consoleState}
                    />
                </aside>
            </div>
            <div className="event-log-area">
                <EventLog events={consoleState?.event_log ?? []} />
            </div>
        </div>
    );
}
