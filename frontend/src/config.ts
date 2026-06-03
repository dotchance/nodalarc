// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Scene constants and configuration.
 *  Visual tokens (colors, sizes) sourced from tokens.ts.
 *  Re-exported here for backwards compatibility with existing consumers. */

import { tokens } from "./styles/tokens";

export const EARTH_RADIUS = tokens.earthRadius;
export const KM_PER_UNIT = 6371 / EARTH_RADIUS;

// Runtime config injected by container entrypoint (config.js), then
// Vite build-time env vars, then auto-derive from browser hostname.
const _cfg = (window as any).NODALARC_CONFIG || {};
const _host = typeof window !== "undefined" ? window.location.hostname : "localhost";

function _numberConfig(name: string, raw: unknown, defaultValue: number): number {
  if (raw === undefined || raw === null || raw === "") return defaultValue;
  const parsed = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(parsed)) {
    throw new Error(`invalid numeric UI config ${name}: ${String(raw)}`);
  }
  return parsed;
}

function _booleanConfig(raw: unknown, defaultValue: boolean): boolean {
  if (raw === undefined || raw === null || raw === "") return defaultValue;
  if (typeof raw === "boolean") return raw;
  if (raw === "true") return true;
  if (raw === "false") return false;
  throw new Error(`invalid boolean UI config cameraFlyToInstant: ${String(raw)}`);
}

export const CAMERA_FLY_TO_SPEED_UNITS_PER_SECOND = _numberConfig(
  "cameraFlyToSpeedUnitsPerSecond",
  _cfg.cameraFlyToSpeedUnitsPerSecond ?? import.meta.env.VITE_CAMERA_FLY_TO_SPEED_UNITS_PER_SECOND,
  1800,
);
export const CAMERA_FLY_TO_MIN_MS = _numberConfig(
  "cameraFlyToMinMs",
  _cfg.cameraFlyToMinMs ?? import.meta.env.VITE_CAMERA_FLY_TO_MIN_MS,
  450,
);
export const CAMERA_FLY_TO_MAX_MS = _numberConfig(
  "cameraFlyToMaxMs",
  _cfg.cameraFlyToMaxMs ?? import.meta.env.VITE_CAMERA_FLY_TO_MAX_MS,
  2200,
);
export const CAMERA_FLY_TO_INSTANT = _booleanConfig(
  _cfg.cameraFlyToInstant ?? import.meta.env.VITE_CAMERA_FLY_TO_INSTANT,
  false,
);

if (CAMERA_FLY_TO_SPEED_UNITS_PER_SECOND <= 0) {
  throw new Error("cameraFlyToSpeedUnitsPerSecond must be > 0");
}
if (CAMERA_FLY_TO_MIN_MS < 0 || CAMERA_FLY_TO_MAX_MS < CAMERA_FLY_TO_MIN_MS) {
  throw new Error("camera fly-to timing must satisfy 0 <= min <= max");
}

// Dev server (vite): route REST/WS through the page origin so the dev proxy
// (VITE_DEV_PROXY_TARGET in vite.config.ts) forwards to a live VS-API — same-origin, no
// CORS, full HMR, no container rebuild. Production keeps host:8080 / NODALARC_CONFIG.
const _devOrigin =
  import.meta.env.DEV && typeof window !== "undefined" ? window.location.origin : null;
export const REST_URL =
  _cfg.vsApiUrl ||
  (import.meta.env.VITE_VSAPI_REST_URL as string) ||
  _devOrigin ||
  `http://${_host}:8080`;

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
const _devWsOrigin =
  import.meta.env.DEV && typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws/v1/state`
    : null;

export function getWsUrl(): string {
  const base =
    _cfg.wsUrl ||
    (import.meta.env.VITE_VSAPI_WS_URL as string) ||
    _devWsOrigin ||
    `ws://${_host}:8080/ws/v1/state`;
  const key = getApiKey();
  return key ? `${base}?token=${encodeURIComponent(key)}` : base;
}

/** WS_URL kept for backwards compatibility (diagnostics display). */
export const WS_URL =
  _cfg.wsUrl ||
  (import.meta.env.VITE_VSAPI_WS_URL as string) ||
  _devWsOrigin ||
  `ws://${_host}:8080/ws/v1/state`;

/** Return headers object with Authorization if API key is set. */
export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  const key = getApiKey();
  if (key) headers["Authorization"] = `Bearer ${key}`;
  return headers;
}

/** Satellite sphere radius in scene units */
export const SAT_RADIUS = tokens.satRadius;
export const SAT_SEGMENTS = tokens.satSegments;

/** Ground station sprite size */
export const GS_SIZE = tokens.gsSize;

/** Camera defaults */
export const CAMERA_FOV = tokens.cameraFov;
export const CAMERA_DISTANCE = tokens.cameraDistance;
export const CAMERA_MIN_DISTANCE = tokens.cameraMinDistance;
export const CAMERA_MAX_DISTANCE = tokens.cameraMaxDistance;

/** Link colors — sourced from tokens */
export const LINK_ISL_COLOR = tokens.colorLinkIsl;
export const LINK_GROUND_COLOR = tokens.colorLinkGround;
export const LINK_FAIL_COLOR = tokens.colorLinkFail;
export const LINK_INACTIVE_COLOR = tokens.colorLinkInactive;
export const LINK_FLOW_COLOR = tokens.colorLinkFlow;
export const LINK_FLOW_SECONDARY_COLOR = tokens.colorLinkFlowSecondary;

/** Link widths (px) */
export const LINK_ISL_WIDTH = tokens.linkWidthIsl;
export const LINK_GROUND_WIDTH = tokens.linkWidthGround;
export const LINK_FLOW_WIDTH = tokens.linkWidthFlow;

/** Area colors — routing area → color mapping */
export const AREA_COLORS: Record<string, number> = {
  "49.0001": tokens.areaRed,
  "49.0002": tokens.areaGreen,
  "49.0003": tokens.areaBlue,
  "49.0004": tokens.areaAmber,
  "0.0.0.0": tokens.areaRed,
  "0.0.0.1": tokens.areaGreen,
  "0.0.0.2": tokens.areaBlue,
  "0.0.0.3": tokens.areaAmber,
};

/** Plane colors */
export const PLANE_COLORS: readonly number[] = tokens.planeColors;

/** Get plane color with lightness reduction for planes beyond the base palette. */
export function getPlaneColor(plane: number): number {
  const base = PLANE_COLORS[plane % PLANE_COLORS.length] ?? 0xaabbcc;
  const cycle = Math.floor(plane / PLANE_COLORS.length);
  if (cycle === 0) return base;
  const factor = Math.max(0.25, 1 - cycle * 0.25);
  const r = Math.round(((base >> 16) & 0xff) * factor);
  const g = Math.round(((base >> 8) & 0xff) * factor);
  const b = Math.round((base & 0xff) * factor);
  return (r << 16) | (g << 8) | b;
}

/** Ground station color */
export const GS_COLOR = tokens.colorNodeGs;

/** Selection highlight */
export const SELECTION_COLOR = tokens.colorNodeSelected;

/** Fail-flash timing */
export const FAIL_HOLD_MS = tokens.failHoldMs;
export const FAIL_FADE_MS = tokens.failFadeMs;
