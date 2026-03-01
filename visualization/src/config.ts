/** Scene constants and configuration. */

export const EARTH_RADIUS = 100;
export const KM_PER_UNIT = 6371 / EARTH_RADIUS;

const _host = typeof window !== "undefined" ? window.location.hostname : "localhost";
export const WS_URL = import.meta.env.VITE_VSAPI_WS_URL as string || `ws://${_host}:8080/ws/v1/state`;
export const REST_URL = import.meta.env.VITE_VSAPI_REST_URL as string || `http://${_host}:8080`;

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

/** Link colors (dimmer so they don't overpower the globe) */
export const LINK_ISL_COLOR = 0x44aa66;
export const LINK_GROUND_COLOR = 0x00bbcc;
export const LINK_FAIL_COLOR = 0xff3333;
export const LINK_INACTIVE_COLOR = 0x333333;
export const LINK_FLOW_COLOR = 0xff8800;

/** Cross-area ISL (dashed white per VF spec) */
export const LINK_CROSS_AREA_COLOR = 0xffffff;
export const LINK_CROSS_AREA_OPACITY = 0.5;

/** Link widths (px) */
export const LINK_ISL_WIDTH = 1.0;
export const LINK_CROSS_AREA_WIDTH = 1.5;
export const LINK_GROUND_WIDTH = 1.5;
export const LINK_FLOW_WIDTH = 3;

/** Area colors (for routing area coloring mode) */
export const AREA_COLORS: Record<string, number> = {
  "49.0001": 0xff4444, // red
  "49.0002": 0x44cc66, // green
  "49.0003": 0x4488ff, // blue
  "49.0004": 0xffaa00, // amber
  "0.0.0.0": 0xff4444,
  "0.0.0.1": 0x44cc66,
  "0.0.0.2": 0x4488ff,
  "0.0.0.3": 0xffaa00,
};

/** Plane colors (for orbital plane coloring mode) */
export const PLANE_COLORS: number[] = [
  0xff4444, // red
  0x44cc66, // green
  0x4488ff, // blue
  0xffaa00, // amber
  0xff44ff, // magenta
  0x44ffff, // cyan
];

/** Ground station color */
export const GS_COLOR = 0x00d4aa;

/** Selection highlight */
export const SELECTION_COLOR = 0xffffff;

/** Fail-flash timing */
export const FAIL_HOLD_MS = 5000;
export const FAIL_FADE_MS = 2000;

/** Snapshot recording interval */
export const SNAPSHOT_INTERVAL_S = 10;

/** Animation */
export const LERP_FACTOR = 0.1;
