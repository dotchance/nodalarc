import { useState, useEffect } from "react";
import type { HistoricalTopologyResponse, NodeStateDetail } from "../types";
import { API_BASE } from "../config";

export function useHistoricalTopology(simTime: string | null): HistoricalTopologyResponse | null {
    const [data, setData] = useState<HistoricalTopologyResponse | null>(null);

    useEffect(() => {
        if (!simTime) { setData(null); return; }
        let cancelled = false;
        fetch(`${API_BASE}/api/v1/topology/at/${encodeURIComponent(simTime)}`)
            .then(r => r.json())
            .then(d => { if (!cancelled) setData(d); })
            .catch(() => {});
        return () => { cancelled = true; };
    }, [simTime]);

    return data;
}

export async function fetchNodeStateAt(
    nodeId: string,
    simTime: string,
): Promise<NodeStateDetail> {
    const r = await fetch(
        `${API_BASE}/api/v1/node/${encodeURIComponent(nodeId)}/state/at/${encodeURIComponent(simTime)}`
    );
    return r.json();
}
