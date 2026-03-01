/** Translate VS-API reason codes into networking language for display. */

/** VF spec Section 8.4 — reason code translation table */
const REASON_MAP: Record<string, string> = {
  vis_gained: "satellites in range",
  vis_lost: "satellites out of range",
  tracking_exceeded: "relative motion too fast",
  terminal_exhausted: "no free terminal",
  scenario_inject_down: "injected failure",
  scenario_inject_up: "injected recovery",
  scenario_reconciliation: "scenario ended, reconciled",
  satellite_loss: "satellite lost",
  gs_below_horizon: "satellite below horizon",
  gs_above_horizon: "satellite in view",
};

export function translateReason(reason: string | null): string {
  if (!reason) return "unknown";
  return REASON_MAP[reason] ?? reason;
}

/** Format milliseconds as human-readable duration. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

/** Format ISO datetime string to compact time. */
export function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toISOString().substring(11, 19);
  } catch {
    return iso;
  }
}
