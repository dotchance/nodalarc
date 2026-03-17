// Console API base URL — uses the page's own origin so it works on any port
// (direct, NodePort, or behind a reverse proxy).
export const API_BASE = window.location.origin;

// Globe deep-link base — resolved from server config at runtime.
// Defaults to same hostname on port 3000 until /api/config responds.
let _globeBase = `http://${window.location.hostname}:3000`;
export function getGlobeBase(): string { return _globeBase; }

// Fetch runtime config from the server (ports from platform.yaml, not hardcoded).
export async function loadConfig(): Promise<void> {
  try {
    const resp = await fetch(`${API_BASE}/api/config`);
    if (resp.ok) {
      const cfg = await resp.json();
      if (cfg.globe_port) {
        _globeBase = `http://${window.location.hostname}:${cfg.globe_port}`;
      }
    }
  } catch { /* Server not ready yet — use defaults */ }
}

// Poll intervals
export const CONSOLE_STATE_POLL_MS = 1000;   // 1 Hz
export const TOPOLOGY_POLL_MS = 2000;         // 0.5 Hz
