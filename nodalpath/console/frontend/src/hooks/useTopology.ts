import { useState, useEffect } from "react";
import type { TopologySnapshot } from "../types";
import { API_BASE, TOPOLOGY_POLL_MS } from "../config";

export function useTopology(): TopologySnapshot | null {
    const [topo, setTopo] = useState<TopologySnapshot | null>(null);

    useEffect(() => {
        let cancelled = false;

        const poll = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/v1/topology/current`);
                if (!r.ok) return;
                const data: TopologySnapshot = await r.json();
                if (!cancelled) setTopo(data);
            } catch { /* keep last */ }
        };

        poll();
        const id = setInterval(poll, TOPOLOGY_POLL_MS);
        return () => { cancelled = true; clearInterval(id); };
    }, []);

    return topo;
}
