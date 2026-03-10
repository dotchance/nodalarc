// Console API base URL — auto-derives from the page's hostname, same pattern as VF.
// During development with `npm run dev`, vite.config.ts proxies /api to localhost:3100.
const _host = window.location.hostname;
const _port = 3100;

export const API_BASE = `http://${_host}:${_port}`;

// Globe deep-link base — for "View in Globe" button on node detail panel
export const GLOBE_BASE = `http://${_host}:3000`;

// Poll intervals
export const CONSOLE_STATE_POLL_MS = 1000;   // 1 Hz — same as 5a
export const TOPOLOGY_POLL_MS = 2000;         // 0.5 Hz — topology changes less frequently
