import { useState, useEffect, useCallback } from "react";
import type { InspectionRunSummary, InspectionRunDetail } from "../types";
import { API_BASE } from "../config";

interface UseInspectionResult {
    latestRun: InspectionRunSummary | null;
    runDetail: InspectionRunDetail | null;
    triggering: boolean;
    available: boolean;
    triggerInspection: (nodeIds?: string[]) => Promise<void>;
    fetchRunDetail: (runId: string) => Promise<void>;
}

export function useInspection(): UseInspectionResult {
    const [latestRun, setLatestRun] = useState<InspectionRunSummary | null>(null);
    const [runDetail, setRunDetail] = useState<InspectionRunDetail | null>(null);
    const [triggering, setTriggering] = useState(false);
    const [available, setAvailable] = useState(true);

    // Fetch latest run on mount
    useEffect(() => {
        let cancelled = false;

        const fetchLatest = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/v1/inspect/latest`);
                if (r.status === 503) {
                    if (!cancelled) setAvailable(false);
                    return;
                }
                if (!r.ok) return;
                const data = await r.json();
                if (!cancelled) {
                    setAvailable(true);
                    setLatestRun(data.run ?? null);
                }
            } catch { /* keep last */ }
        };

        fetchLatest();
        const id = setInterval(fetchLatest, 5000);
        return () => { cancelled = true; clearInterval(id); };
    }, []);

    const triggerInspection = useCallback(async (nodeIds?: string[]) => {
        setTriggering(true);
        try {
            const body = nodeIds ? JSON.stringify({ node_ids: nodeIds }) : "{}";
            const r = await fetch(`${API_BASE}/api/v1/inspect/trigger`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body,
            });
            if (!r.ok) return;
            const data = await r.json();
            const runId = data.run_id;

            // Fetch the completed run detail
            const detailR = await fetch(`${API_BASE}/api/v1/inspect/runs/${runId}`);
            if (detailR.ok) {
                const detail: InspectionRunDetail = await detailR.json();
                setRunDetail(detail);
                setLatestRun(detail);
            }
        } catch { /* swallow */ }
        finally {
            setTriggering(false);
        }
    }, []);

    const fetchRunDetail = useCallback(async (runId: string) => {
        try {
            const r = await fetch(`${API_BASE}/api/v1/inspect/runs/${runId}`);
            if (!r.ok) return;
            const detail: InspectionRunDetail = await r.json();
            setRunDetail(detail);
        } catch { /* swallow */ }
    }, []);

    return { latestRun, runDetail, triggering, available, triggerInspection, fetchRunDetail };
}
