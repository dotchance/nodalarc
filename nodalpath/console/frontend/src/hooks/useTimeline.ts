import { useState, useEffect } from "react";
import type { TimelineResponse } from "../types";
import { API_BASE } from "../config";

const TIMELINE_POLL_MS = 2000;

export function useTimeline(): TimelineResponse | null {
    const [data, setData] = useState<TimelineResponse | null>(null);

    useEffect(() => {
        let cancelled = false;
        const poll = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/v1/timeline`);
                if (!r.ok) return;
                const d: TimelineResponse = await r.json();
                if (!cancelled) setData(d);
            } catch { /* keep last */ }
        };
        poll();
        const id = setInterval(poll, TIMELINE_POLL_MS);
        return () => { cancelled = true; clearInterval(id); };
    }, []);

    return data;
}
