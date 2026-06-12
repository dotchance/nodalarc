// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

// Single source of truth for all visual tokens.
// Two themes (Mission Light is the default working console; NOC Dark is the
// high-contrast projection theme). The active theme is resolved ONCE at module
// load from localStorage; switching themes stores the new name and reloads —
// scene materials, canvas paints, and xterm capture token values at
// construction time, so a reload is the honest switch mechanism.
//
// Three.js / canvas / xterm code imports values from `tokens` directly.
// CSS uses var(--token-name) after applyTheme() runs (main.tsx, before mount).
// Change a value here → updates globe, topology, panels, everything.

export type ThemeName = "mission-light" | "noc-dark";

export const THEME_STORAGE_KEY = "nodalarc.theme";

/** Values that differ between themes. Everything else is theme-invariant. */
export interface Theme {
  // surfaces
  bgMain: string;
  bgBar: string;
  bgPanel: string;
  bgPanelHover: string;
  border: string;
  borderStrong: string;
  // text
  textPrimary: string;
  textSecondary: string;
  textDim: string;
  // accents
  accentBlue: string;
  accentTeal: string;
  accentOrange: string;
  accentRed: string;
  accentGreen: string;
  accentAmber: string;
  // generic status slots (transport health, convergence, severity fills)
  statusOk: string;
  statusWarn: string;
  statusFail: string;
  // decision-family tones (the Expected/Faulted color law, single source).
  // faulted is the ONLY red; a restrictive or intermittent model must read
  // as calm, never as an error. Law enforced by explain registry tests.
  familyConnected: string;
  familyExpectedNoLink: string;
  familyEligibleUnselected: string;
  familyInFlight: string;
  familyFaulted: string;
  familyUnknown: string;
  // scene
  colorLinkFail: number; // matches familyFaulted — beams and cards agree on red
  shadowPopover: string;
  shadowPanel: string;
}

export const THEMES: Record<ThemeName, Theme> = {
  "mission-light": {
    bgMain: "#0d1117",
    bgBar: "#121820",
    bgPanel: "#1c2430",
    bgPanelHover: "#222c39",
    border: "#3c4858",
    borderStrong: "#5b6b80",
    textPrimary: "#f6f8fb",
    textSecondary: "#c2ccd9",
    textDim: "#8c98a8",
    accentBlue: "#82b7ff",
    accentTeal: "#7ed4df",
    accentOrange: "#f2a34b",
    accentRed: "#f06c6c",
    accentGreen: "#68d98b",
    accentAmber: "#f0b84d",
    statusOk: "#68d98b",
    statusWarn: "#f0b84d",
    statusFail: "#f06c6c",
    familyConnected: "#68d98b",
    // calm steel-blue "no-link is fine" tone: holds contrast as a globe glyph
    // against both dark ocean and tan land.
    familyExpectedNoLink: "#7aa7e8",
    familyEligibleUnselected: "#5b9aa8",
    familyInFlight: "#f0b84d",
    familyFaulted: "#f06c6c",
    familyUnknown: "#8a93a8",
    colorLinkFail: 0xf06c6c,
    shadowPopover: "0 4px 16px rgba(0, 0, 0, 0.38)",
    shadowPanel: "0 4px 12px rgba(0, 0, 0, 0.3)",
  },
  "noc-dark": {
    bgMain: "#050607",
    bgBar: "#090b0d",
    bgPanel: "#111820",
    bgPanelHover: "#182331",
    border: "#435266",
    borderStrong: "#65768c",
    textPrimary: "#ffffff",
    textSecondary: "#c0cad6",
    textDim: "#8f9cad",
    accentBlue: "#7db5ff",
    accentTeal: "#77dce8",
    accentOrange: "#f2a34b",
    accentRed: "#ff6b6b",
    accentGreen: "#6be58f",
    accentAmber: "#ffc452",
    statusOk: "#6be58f",
    statusWarn: "#ffc452",
    statusFail: "#ff6b6b",
    familyConnected: "#6be58f",
    familyExpectedNoLink: "#84aff0",
    familyEligibleUnselected: "#5fa8b8",
    familyInFlight: "#ffc452",
    familyFaulted: "#ff6b6b",
    familyUnknown: "#93a0b3",
    colorLinkFail: 0xff6b6b,
    shadowPopover: "0 4px 16px rgba(0, 0, 0, 0.55)",
    shadowPanel: "0 4px 12px rgba(0, 0, 0, 0.45)",
  },
};

export function activeThemeName(): ThemeName {
  if (typeof localStorage !== "undefined") {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "mission-light" || stored === "noc-dark") return stored;
    if (stored !== null) {
      console.warn(`unknown theme '${stored}' in ${THEME_STORAGE_KEY}; using mission-light`);
    }
  }
  return "mission-light";
}

const theme = THEMES[activeThemeName()];

/** Derive an rgba() string from a 6-digit hex token (for JS-side consumers;
 *  CSS-side derived tints use color-mix() in stylesheets instead). */
export function withAlpha(hexCss: string, alpha: number): string {
  const v = parseInt(hexCss.replace("#", ""), 16);
  const r = (v >> 16) & 0xff;
  const g = (v >> 8) & 0xff;
  const b = v & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export const tokens = {
  // --- Theme-resolved values (see Theme above) ---
  ...theme,

  // --- Derived surfaces ---
  bgToolbar: withAlpha(theme.bgPanel, 0.9),
  bgOverlay96: withAlpha(theme.bgPanel, 0.96),
  bgOverlay94: withAlpha(theme.bgPanel, 0.94),
  bgOverlay90: withAlpha(theme.bgPanel, 0.9),
  bgScrim: withAlpha(theme.bgMain, 0.95),
  bgScrimLight: withAlpha(theme.bgMain, 0.6),
  bgSwitching: "rgba(0, 0, 0, 0.7)",

  // --- Taxonomy: orbital regime (theme-invariant identity colors) ---
  regimeLeo: "#f2a34b",
  regimeMeo: "#64d1df",
  regimeGeo: "#68d98b",
  regimeHeo: "#82b7ff",
  regimeLuna: "#c9c4bb",

  // --- Taxonomy: link medium (theme-invariant) ---
  mediumRf: "#f0c15c",
  mediumOptical: "#aa98ff",
  mediumTerrestrial: "#9aa6b5",

  // --- Node colors (Three.js hex) ---
  colorNodeSatellite: 0xccddee,
  colorNodeGs: 0x7ed4df,
  colorNodeSelected: 0xffffff,
  colorNodeUnknown: 0xaabbcc, // unmapped area/plane fallback tint
  colorBodyPolitical: 0x18202b, // matte schematic globe (political mode)
  colorTrail: 0x6699dd, // satellite motion-history trails
  colorFootprint: 0x8edcff, // selected-sat coverage disc (access-relation family)
  colorBoundaries: 0x88aacc, // country border lines on the globe
  colorSunTint: 0xfff1a8, // sun reference point

  // --- Link colors (Three.js hex). Relation identity: ISL violet, access
  //     light-blue, inter-body warm grey. State stays a separate channel
  //     (proven solid / unproven dimmed / failing red / inactive hidden). ---
  colorLinkIsl: 0x9f91ff,
  colorLinkGround: 0x8edcff,
  colorLinkInterbody: 0xd7d4cc,
  colorLinkInactive: 0x3a4452,
  colorLinkFlow: 0xf2a34b,
  colorLinkFlowSecondary: 0x7ed4df,

  // --- Link widths (px) ---
  linkWidthIsl: 1.5,
  linkWidthGround: 2,
  linkWidthFlow: 4,

  // --- Area colors (IS-IS/OSPF routing areas, Three.js hex) ---
  areaRed: 0xcc4444,
  areaGreen: 0x44aa44,
  areaBlue: 0x4477bb,
  areaAmber: 0xcc8844,

  // --- Plane colors (orbital planes, Three.js hex) ---
  planeColors: [
    0xe06666, // red
    0xe09c66, // orange
    0xd4cc66, // yellow
    0x66c266, // green
    0x6699cc, // blue
    0x9966cc, // purple
  ] as readonly number[],

  // --- Fail-flash timing (ms) — drives scene animation AND state expiry ---
  failHoldMs: 1500,
  failFadeMs: 1000,

  // --- Typography. Inter (UI) + IBM Plex Mono (data/CLI) are self-hosted
  //     (fonts.css). fontFamily is the body font: still the mono face until
  //     the shell stage flips it to fontFamilyUi surface-by-surface. ---
  fontFamilyUi: "'Inter', system-ui, sans-serif",
  fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
  fontFamilyCli: "'IBM Plex Mono', ui-monospace, monospace",
  fontSizeXxs: "10px",
  fontSizeXs: "11px",
  fontSizeSm: "12px",
  fontSizeMd: "13px",
  fontSizeLg: "14px",
  fontSizeXl: "16px",
  fontSizeXxl: "20px",
  fontSizeTitle: "28px",
  fontWeightNormal: 400,
  fontWeightMedium: 500,
  fontWeightSemibold: 600,
  fontWeightBold: 700,

  // --- Spacing scale ---
  space1: "2px",
  space2: "4px",
  space3: "6px",
  space4: "8px",
  space5: "10px",
  space6: "12px",
  space8: "16px",
  space10: "20px",
  space12: "24px",
  space16: "32px",
  space24: "48px",

  // --- Z-index layers ---
  zPanel: 10,
  zCliDrawer: 15,
  zPopover: 20,
  zCatalog: 50,
  zOverlay: 100,
  zScrim: 200,
  zTooltip: 400,
  zWindow: 500, // floating operational windows (logs, trace) ride above all chrome

  // Scene-local DOM ladder (inside the viewport's own stacking context):
  // labels (none) < hud < seek overlay < scene tooltip.
  zSceneHud: 8,
  zSceneSeek: 10,
  zSceneTooltip: 20,

  // --- Border radii ---
  radiusXs: "2px",
  radiusSm: "3px",
  radiusMd: "4px",
  radiusLg: "6px",
  radiusXl: "8px",
  radiusPill: "20px",
  radiusCircle: "50%",

  // --- Transitions ---
  transitionFast: "0.15s ease",
  transitionNormal: "0.2s ease",

  // --- Touch targets ---
  touchTargetMin: "44px",
  touchTargetComfortable: "48px",

  // --- Layout ---
  topbarHeight: "48px",
  bottombarHeight: "32px",
  panelWidth: "420px",

  // --- Scene constants (Three.js units, not CSS). BEHAVIOR, not theme:
  //     earthRadius pairs with orbitalMath.SCENE_EARTH_RADIUS (physics→scene
  //     scale) and cameraDistance participates in the label-fade rendering
  //     invariant. Never vary these per theme. ---
  earthRadius: 100,
  satRadius: 0.9,
  satSegments: 12,
  gsSize: 2.1,
  cameraFov: 45,
  cameraDistance: 250,
  cameraMinDistance: 105,
  cameraMaxDistance: 3000,
};

export type Tokens = typeof tokens;

/** Write the active theme's tokens onto :root as CSS custom properties.
 *  Must run before first paint (main.tsx). */
export function applyTheme(): void {
  const s = document.documentElement.style;

  // Surfaces
  s.setProperty("--bg-main", tokens.bgMain);
  s.setProperty("--bg-panel", tokens.bgPanel);
  s.setProperty("--bg-panel-hover", tokens.bgPanelHover);
  s.setProperty("--bg-toolbar", tokens.bgToolbar);
  s.setProperty("--bg-bar", tokens.bgBar);
  s.setProperty("--bg-overlay-96", tokens.bgOverlay96);
  s.setProperty("--bg-overlay-94", tokens.bgOverlay94);
  s.setProperty("--bg-overlay-90", tokens.bgOverlay90);
  s.setProperty("--bg-scrim", tokens.bgScrim);
  s.setProperty("--bg-scrim-light", tokens.bgScrimLight);
  s.setProperty("--bg-switching", tokens.bgSwitching);

  // Text
  s.setProperty("--text-primary", tokens.textPrimary);
  s.setProperty("--text-secondary", tokens.textSecondary);
  s.setProperty("--text-dim", tokens.textDim);

  // Borders
  s.setProperty("--border", tokens.border);
  s.setProperty("--border-strong", tokens.borderStrong);

  // Accents
  s.setProperty("--accent-blue", tokens.accentBlue);
  s.setProperty("--accent-teal", tokens.accentTeal);
  s.setProperty("--accent-orange", tokens.accentOrange);
  s.setProperty("--accent-red", tokens.accentRed);
  s.setProperty("--accent-green", tokens.accentGreen);
  s.setProperty("--accent-amber", tokens.accentAmber);

  // Status slots
  s.setProperty("--status-ok", tokens.statusOk);
  s.setProperty("--status-warn", tokens.statusWarn);
  s.setProperty("--status-fail", tokens.statusFail);

  // Typography
  s.setProperty("--font-ui", tokens.fontFamilyUi);
  s.setProperty("--font-family", tokens.fontFamily);
  s.setProperty("--font-family-cli", tokens.fontFamilyCli);
  s.setProperty("--font-mono", tokens.fontFamilyCli);
  s.setProperty("--font-size-xxs", tokens.fontSizeXxs);
  s.setProperty("--font-size-xs", tokens.fontSizeXs);
  s.setProperty("--font-size-sm", tokens.fontSizeSm);
  s.setProperty("--font-size-md", tokens.fontSizeMd);
  s.setProperty("--font-size-lg", tokens.fontSizeLg);
  s.setProperty("--font-size-xl", tokens.fontSizeXl);
  s.setProperty("--font-size-xxl", tokens.fontSizeXxl);
  s.setProperty("--font-size-title", tokens.fontSizeTitle);
  s.setProperty("--font-weight-normal", String(tokens.fontWeightNormal));
  s.setProperty("--font-weight-medium", String(tokens.fontWeightMedium));
  s.setProperty("--font-weight-semibold", String(tokens.fontWeightSemibold));
  s.setProperty("--font-weight-bold", String(tokens.fontWeightBold));

  // Spacing
  s.setProperty("--space-1", tokens.space1);
  s.setProperty("--space-2", tokens.space2);
  s.setProperty("--space-3", tokens.space3);
  s.setProperty("--space-4", tokens.space4);
  s.setProperty("--space-5", tokens.space5);
  s.setProperty("--space-6", tokens.space6);
  s.setProperty("--space-8", tokens.space8);
  s.setProperty("--space-10", tokens.space10);
  s.setProperty("--space-12", tokens.space12);
  s.setProperty("--space-16", tokens.space16);
  s.setProperty("--space-24", tokens.space24);

  // Z-index
  s.setProperty("--z-panel", String(tokens.zPanel));
  s.setProperty("--z-cli-drawer", String(tokens.zCliDrawer));
  s.setProperty("--z-popover", String(tokens.zPopover));
  s.setProperty("--z-catalog", String(tokens.zCatalog));
  s.setProperty("--z-overlay", String(tokens.zOverlay));
  s.setProperty("--z-scrim", String(tokens.zScrim));
  s.setProperty("--z-tooltip", String(tokens.zTooltip));
  s.setProperty("--z-window", String(tokens.zWindow));
  s.setProperty("--z-scene-hud", String(tokens.zSceneHud));
  s.setProperty("--z-scene-seek", String(tokens.zSceneSeek));
  s.setProperty("--z-scene-tooltip", String(tokens.zSceneTooltip));

  // Border radii
  s.setProperty("--radius-xs", tokens.radiusXs);
  s.setProperty("--radius-sm", tokens.radiusSm);
  s.setProperty("--radius-md", tokens.radiusMd);
  s.setProperty("--radius-lg", tokens.radiusLg);
  s.setProperty("--radius-xl", tokens.radiusXl);
  s.setProperty("--radius-pill", tokens.radiusPill);
  s.setProperty("--radius-circle", tokens.radiusCircle);

  // Shadows
  s.setProperty("--shadow-popover", tokens.shadowPopover);
  s.setProperty("--shadow-panel", tokens.shadowPanel);

  // Transitions
  s.setProperty("--transition-fast", tokens.transitionFast);
  s.setProperty("--transition-normal", tokens.transitionNormal);

  // Touch targets
  s.setProperty("--touch-target-min", tokens.touchTargetMin);
  s.setProperty("--touch-target-comfortable", tokens.touchTargetComfortable);

  // Layout
  s.setProperty("--topbar-height", tokens.topbarHeight);
  s.setProperty("--bottombar-height", tokens.bottombarHeight);
  s.setProperty("--panel-width", tokens.panelWidth);
}
