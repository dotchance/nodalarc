import { useState, useCallback } from "react";
import type { PathResult } from "../types";
import { API_BASE } from "../config";

export function usePath() {
    const [result, setResult] = useState<PathResult | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const query = useCallback(async (
        src: string,
        dst: string,
        simTime: string | null = null,
    ) => {
        if (!src || !dst || src === dst) return;
        setLoading(true);
        setError(null);
        try {
            const params = new URLSearchParams({ src, dst });
            if (simTime) params.set("sim_time", simTime);
            const r = await fetch(`${API_BASE}/api/v1/path?${params}`);
            const data: PathResult = await r.json();
            setResult(data);
        } catch (e) {
            setError("Failed to fetch path");
            setResult(null);
        } finally {
            setLoading(false);
        }
    }, []);

    const clear = useCallback(() => {
        setResult(null);
        setError(null);
    }, []);

    return { result, loading, error, query, clear };
}
