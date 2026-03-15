import { useState, useEffect } from "react";
import type { TraceConfig } from "../types";
import { API_BASE } from "../config";

export function useTraceConfig(): TraceConfig | null {
    const [config, setConfig] = useState<TraceConfig | null>(null);

    useEffect(() => {
        fetch(`${API_BASE}/api/v1/trace-config`)
            .then(r => r.json())
            .then((data: TraceConfig) => setConfig(data))
            .catch(() => setConfig(null));
    }, []);

    return config;
}
