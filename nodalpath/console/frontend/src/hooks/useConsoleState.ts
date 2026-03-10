import { useState, useEffect } from "react";
import type { ConsoleStateSnapshot } from "../types";
import { API_BASE, CONSOLE_STATE_POLL_MS } from "../config";

export function useConsoleState(): ConsoleStateSnapshot | null {
    const [state, setState] = useState<ConsoleStateSnapshot | null>(null);

    useEffect(() => {
        let cancelled = false;

        const poll = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/status`);
                if (!r.ok) return;
                const data: ConsoleStateSnapshot = await r.json();
                if (!cancelled) setState(data);
            } catch { /* network error — keep last state */ }
        };

        poll();
        const id = setInterval(poll, CONSOLE_STATE_POLL_MS);
        return () => { cancelled = true; clearInterval(id); };
    }, []);

    return state;
}
