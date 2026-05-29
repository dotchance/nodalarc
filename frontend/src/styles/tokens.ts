// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.

// Single source of truth for all visual tokens.
// Three.js code imports hex values directly.
// CSS code uses var(--token-name) after injectCssTokens() runs.
// Change a value here → updates globe, topology, panels, everything.

export const tokens = {
  // --- Backgrounds ---
  bgMain: '#0d0d1a',
  bgPanel: '#1a1a2e',
  bgPanelHover: '#2a2a4e',
  bgToolbar: 'rgba(26, 26, 46, 0.8)',
  bgBar: '#16162a',
  bgOverlay96: 'rgba(26, 26, 46, 0.96)',
  bgOverlay94: 'rgba(26, 26, 46, 0.94)',
  bgOverlay92: 'rgba(26, 26, 46, 0.92)',
  bgOverlay90: 'rgba(26, 26, 46, 0.9)',
  bgScrim: 'rgba(13, 13, 26, 0.95)',
  bgScrimLight: 'rgba(13, 13, 26, 0.6)',
  bgSwitching: 'rgba(0, 0, 0, 0.7)',

  // --- Text ---
  textPrimary: '#e0e0e0',
  textSecondary: '#888899',
  textDim: '#7777aa',

  // --- Borders ---
  border: '#2a2a4e',
  borderSubtle: '#2a2a4a',

  // --- Accents ---
  accentBlue: '#4488ff',
  accentTeal: '#00d4aa',
  accentOrange: '#ff8800',
  accentRed: '#ff3333',
  accentGreen: '#44cc66',
  accentAmber: '#ffaa00',

  // --- Node colors (Three.js hex) ---
  colorNodeSatellite: 0xccddee,
  colorNodeGs: 0x00d4aa,
  colorNodeSelected: 0xffffff,

  // --- Link colors (Three.js hex) ---
  colorLinkIsl: 0x44cc66,
  colorLinkGround: 0x00ccff,
  colorLinkFail: 0xff3333,
  colorLinkInactive: 0x333333,
  colorLinkFlow: 0xff8800,
  colorLinkFlowSecondary: 0xff00aa,

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

  // --- Status ---
  statusConnected: '#44cc66',
  statusReconnecting: '#ffaa00',
  statusDisconnected: '#ff3333',
  statusError: '#e85555',
  statusErrorBright: '#ff6666',

  // --- Decision family tones (the Expected/Faulted color law, single source) ---
  // The canonical no-link families. faulted is the ONLY red; a restrictive or
  // intermittent model must read as calm, never as an error. statusDisconnected
  // above is red and encodes the OLD connected/disconnected model — surfaces are
  // migrating onto these family tones so a correct (expected) no-link is not red.
  familyConnected: '#44cc66',
  familyExpectedNoLink: '#7788aa',
  familyEligibleUnselected: '#5b9aa8',
  familyInFlight: '#ffaa00',
  familyFaulted: '#ff3333',
  familyUnknown: '#8a87a8',

  // --- Fail-flash timing (ms) ---
  failHoldMs: 1500,
  failFadeMs: 1000,

  // --- Typography ---
  fontFamily: "'JetBrains Mono', 'Fira Code', 'Source Code Pro', monospace",
  fontFamilyCli: "'SF Mono', 'Fira Code', 'Cascadia Code', monospace",
  fontSizeXxs: '10px',
  fontSizeXs: '11px',
  fontSizeSm: '12px',
  fontSizeMd: '13px',
  fontSizeLg: '14px',
  fontSizeXl: '16px',
  fontSizeXxl: '20px',
  fontSizeTitle: '28px',
  fontWeightNormal: 400,
  fontWeightMedium: 500,
  fontWeightSemibold: 600,
  fontWeightBold: 700,

  // --- Spacing scale ---
  space1: '2px',
  space2: '4px',
  space3: '6px',
  space4: '8px',
  space5: '10px',
  space6: '12px',
  space8: '16px',
  space10: '20px',
  space12: '24px',
  space16: '32px',
  space24: '48px',

  // --- Z-index layers ---
  zPanel: 10,
  zCliDrawer: 15,
  zPopover: 20,
  zCatalog: 50,
  zOverlay: 100,
  zScrim: 200,
  zToast: 300,
  zTooltip: 400,

  // --- Border radii ---
  radiusXs: '2px',
  radiusSm: '3px',
  radiusMd: '4px',
  radiusLg: '6px',
  radiusXl: '8px',
  radiusPill: '20px',
  radiusCircle: '50%',

  // --- Shadows ---
  shadowPopover: '0 4px 16px rgba(0, 0, 0, 0.4)',
  shadowPanel: '0 4px 12px rgba(0, 0, 0, 0.3)',

  // --- Transitions ---
  transitionFast: '0.15s ease',
  transitionNormal: '0.2s ease',

  // --- Touch targets ---
  touchTargetMin: '44px',
  touchTargetComfortable: '48px',

  // --- Responsive breakpoints ---
  breakpointTablet: '768px',
  breakpointDesktop: '1280px',

  // --- Layout ---
  topbarHeight: '48px',
  bottombarHeight: '32px',
  panelWidth: '420px',
  toolbarWidth: '48px',

  // --- Scene constants (Three.js units, not CSS) ---
  earthRadius: 100,
  satRadius: 0.6,
  satSegments: 12,
  gsSize: 1.6,
  cameraFov: 45,
  cameraDistance: 250,
  cameraMinDistance: 105,
  cameraMaxDistance: 1200,
} as const;

export type Tokens = typeof tokens;

function hexToCSS(hex: number): string {
  return '#' + hex.toString(16).padStart(6, '0');
}

export function injectCssTokens(): void {
  const s = document.documentElement.style;

  // Backgrounds
  s.setProperty('--bg-main', tokens.bgMain);
  s.setProperty('--bg-panel', tokens.bgPanel);
  s.setProperty('--bg-panel-hover', tokens.bgPanelHover);
  s.setProperty('--bg-toolbar', tokens.bgToolbar);
  s.setProperty('--bg-bar', tokens.bgBar);
  s.setProperty('--bg-overlay-96', tokens.bgOverlay96);
  s.setProperty('--bg-overlay-94', tokens.bgOverlay94);
  s.setProperty('--bg-overlay-92', tokens.bgOverlay92);
  s.setProperty('--bg-overlay-90', tokens.bgOverlay90);
  s.setProperty('--bg-scrim', tokens.bgScrim);
  s.setProperty('--bg-scrim-light', tokens.bgScrimLight);
  s.setProperty('--bg-switching', tokens.bgSwitching);

  // Text
  s.setProperty('--text-primary', tokens.textPrimary);
  s.setProperty('--text-secondary', tokens.textSecondary);
  s.setProperty('--text-dim', tokens.textDim);

  // Borders
  s.setProperty('--border', tokens.border);
  s.setProperty('--border-subtle', tokens.borderSubtle);

  // Accents
  s.setProperty('--accent-blue', tokens.accentBlue);
  s.setProperty('--accent-teal', tokens.accentTeal);
  s.setProperty('--accent-orange', tokens.accentOrange);
  s.setProperty('--accent-red', tokens.accentRed);
  s.setProperty('--accent-green', tokens.accentGreen);
  s.setProperty('--accent-amber', tokens.accentAmber);

  // Node/link colors (CSS versions for topology view, panels)
  s.setProperty('--color-node-satellite', hexToCSS(tokens.colorNodeSatellite));
  s.setProperty('--color-node-gs', hexToCSS(tokens.colorNodeGs));
  s.setProperty('--color-node-selected', hexToCSS(tokens.colorNodeSelected));
  s.setProperty('--color-link-isl', hexToCSS(tokens.colorLinkIsl));
  s.setProperty('--color-link-ground', hexToCSS(tokens.colorLinkGround));
  s.setProperty('--color-link-fail', hexToCSS(tokens.colorLinkFail));
  s.setProperty('--color-link-flow', hexToCSS(tokens.colorLinkFlow));

  // Status
  s.setProperty('--ws-connected', tokens.statusConnected);
  s.setProperty('--ws-reconnecting', tokens.statusReconnecting);
  s.setProperty('--ws-disconnected', tokens.statusDisconnected);
  s.setProperty('--status-error', tokens.statusError);

  // Typography
  s.setProperty('--font-family', tokens.fontFamily);
  s.setProperty('--font-family-cli', tokens.fontFamilyCli);
  s.setProperty('--font-size-xxs', tokens.fontSizeXxs);
  s.setProperty('--font-size-xs', tokens.fontSizeXs);
  s.setProperty('--font-size-sm', tokens.fontSizeSm);
  s.setProperty('--font-size-md', tokens.fontSizeMd);
  s.setProperty('--font-size-lg', tokens.fontSizeLg);
  s.setProperty('--font-size-xl', tokens.fontSizeXl);
  s.setProperty('--font-size-xxl', tokens.fontSizeXxl);
  s.setProperty('--font-size-title', tokens.fontSizeTitle);
  s.setProperty('--font-weight-normal', String(tokens.fontWeightNormal));
  s.setProperty('--font-weight-medium', String(tokens.fontWeightMedium));
  s.setProperty('--font-weight-semibold', String(tokens.fontWeightSemibold));
  s.setProperty('--font-weight-bold', String(tokens.fontWeightBold));

  // Spacing
  s.setProperty('--space-1', tokens.space1);
  s.setProperty('--space-2', tokens.space2);
  s.setProperty('--space-3', tokens.space3);
  s.setProperty('--space-4', tokens.space4);
  s.setProperty('--space-5', tokens.space5);
  s.setProperty('--space-6', tokens.space6);
  s.setProperty('--space-8', tokens.space8);
  s.setProperty('--space-10', tokens.space10);
  s.setProperty('--space-12', tokens.space12);
  s.setProperty('--space-16', tokens.space16);
  s.setProperty('--space-24', tokens.space24);

  // Z-index
  s.setProperty('--z-panel', String(tokens.zPanel));
  s.setProperty('--z-cli-drawer', String(tokens.zCliDrawer));
  s.setProperty('--z-popover', String(tokens.zPopover));
  s.setProperty('--z-catalog', String(tokens.zCatalog));
  s.setProperty('--z-overlay', String(tokens.zOverlay));
  s.setProperty('--z-scrim', String(tokens.zScrim));
  s.setProperty('--z-toast', String(tokens.zToast));
  s.setProperty('--z-tooltip', String(tokens.zTooltip));

  // Border radii
  s.setProperty('--radius-xs', tokens.radiusXs);
  s.setProperty('--radius-sm', tokens.radiusSm);
  s.setProperty('--radius-md', tokens.radiusMd);
  s.setProperty('--radius-lg', tokens.radiusLg);
  s.setProperty('--radius-xl', tokens.radiusXl);
  s.setProperty('--radius-pill', tokens.radiusPill);
  s.setProperty('--radius-circle', tokens.radiusCircle);

  // Shadows
  s.setProperty('--shadow-popover', tokens.shadowPopover);
  s.setProperty('--shadow-panel', tokens.shadowPanel);

  // Transitions
  s.setProperty('--transition-fast', tokens.transitionFast);
  s.setProperty('--transition-normal', tokens.transitionNormal);

  // Touch targets
  s.setProperty('--touch-target-min', tokens.touchTargetMin);
  s.setProperty('--touch-target-comfortable', tokens.touchTargetComfortable);

  // Layout
  s.setProperty('--topbar-height', tokens.topbarHeight);
  s.setProperty('--bottombar-height', tokens.bottombarHeight);
  s.setProperty('--panel-width', tokens.panelWidth);
  s.setProperty('--toolbar-width', tokens.toolbarWidth);
}
