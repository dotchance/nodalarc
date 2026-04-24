// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
/** Scene constants and configuration. */

export const EARTH_RADIUS = 100;
export const KM_PER_UNIT = 6371 / EARTH_RADIUS;

// Runtime config injected by container entrypoint (config.js), then
// Vite build-time env vars, then auto-derive from browser hostname.
const _cfg = (window as any).NODALARC_CONFIG || {};
const _host = typeof window !== "undefined" ? window.location.hostname : "localhost";
export const REST_URL = _cfg.vsApiUrl || import.meta.env.VITE_VSAPI_REST_URL as string || `http://${_host}:8080`;

/** API key for authentication. Read from env at build time or sessionStorage at runtime. */
function _getApiKey(): string {
  const envKey = import.meta.env.VITE_API_KEY as string | undefined;
  if (envKey) return envKey;
  if (typeof sessionStorage !== "undefined") {
    return sessionStorage.getItem("nodal_api_key") ?? "";
  }
  return "";
}

export function getApiKey(): string {
  return _getApiKey();
}

export function setApiKey(key: string): void {
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.setItem("nodal_api_key", key);
  }
}

/** Fetch API key from VS-API token endpoint and store it. */
export async function fetchApiKey(): Promise<string> {
  try {
    const resp = await fetch(`${REST_URL}/api/v1/auth/token`);
    if (resp.ok) {
      const data = await resp.json();
      if (data.token) {
        setApiKey(data.token);
        return data.token;
      }
    }
  } catch { /* VS-API not reachable yet */ }
  return getApiKey();
}

/** Build WebSocket URL with auth token as query parameter. */
export function getWsUrl(): string {
  const base = _cfg.wsUrl || import.meta.env.VITE_VSAPI_WS_URL as string || `ws://${_host}:8080/ws/v1/state`;
  const key = getApiKey();
  return key ? `${base}?token=${encodeURIComponent(key)}` : base;
}

/** WS_URL kept for backwards compatibility (diagnostics display). */
export const WS_URL = _cfg.wsUrl || import.meta.env.VITE_VSAPI_WS_URL as string || `ws://${_host}:8080/ws/v1/state`;

/** Return headers object with Authorization if API key is set. */
export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  const key = getApiKey();
  if (key) headers["Authorization"] = `Bearer ${key}`;
  return headers;
}

/** Satellite sphere radius in scene units */
export const SAT_RADIUS = 0.6;
export const SAT_SEGMENTS = 12;

/** Ground station sprite size */
export const GS_SIZE = 1.6;

/** Camera defaults */
export const CAMERA_FOV = 45;
export const CAMERA_DISTANCE = EARTH_RADIUS * 2.5;
export const CAMERA_MIN_DISTANCE = EARTH_RADIUS * 1.05;
export const CAMERA_MAX_DISTANCE = EARTH_RADIUS * 6;

/** Link colors — VF spec Section 10.2 */
export const LINK_ISL_COLOR = 0x44cc66;
export const LINK_GROUND_COLOR = 0x00ccff;
export const LINK_FAIL_COLOR = 0xff3333;
export const LINK_INACTIVE_COLOR = 0x333333;
export const LINK_FLOW_COLOR = 0xff8800;
export const LINK_FLOW_SECONDARY_COLOR = 0xff00aa;

/** Link widths (px) — VF spec Sections 7.3, 7.4, 7.5 */
export const LINK_ISL_WIDTH = 1.5;
export const LINK_GROUND_WIDTH = 2;
export const LINK_FLOW_WIDTH = 4;

/** Area colors — VF spec Section 10.1 */
export const AREA_COLORS: Record<string, number> = {
  "49.0001": 0xcc4444, // deep red
  "49.0002": 0x44aa44, // deep green
  "49.0003": 0x4477bb, // deep blue
  "49.0004": 0xcc8844, // deep amber
  "0.0.0.0": 0xcc4444,
  "0.0.0.1": 0x44aa44,
  "0.0.0.2": 0x4477bb,
  "0.0.0.3": 0xcc8844,
};

/** Plane colors — VF spec Section 10.1 */
export const PLANE_COLORS: number[] = [
  0xe06666, // red
  0xe09c66, // orange
  0xd4cc66, // yellow
  0x66c266, // green
  0x6699cc, // blue
  0x9966cc, // purple
];

/** Get plane color with lightness reduction for planes beyond the base palette. */
export function getPlaneColor(plane: number): number {
  const base = PLANE_COLORS[plane % PLANE_COLORS.length] ?? 0xaabbcc;
  const cycle = Math.floor(plane / PLANE_COLORS.length);
  if (cycle === 0) return base;
  // Reduce lightness by 25% per cycle
  const factor = Math.max(0.25, 1 - cycle * 0.25);
  const r = Math.round(((base >> 16) & 0xff) * factor);
  const g = Math.round(((base >> 8) & 0xff) * factor);
  const b = Math.round((base & 0xff) * factor);
  return (r << 16) | (g << 8) | b;
}

/** Ground station color */
export const GS_COLOR = 0x00d4aa;

/** Selection highlight */
export const SELECTION_COLOR = 0xffffff;

/** Fail-flash timing */
export const FAIL_HOLD_MS = 1500;
export const FAIL_FADE_MS = 1000;
