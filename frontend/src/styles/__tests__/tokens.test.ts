// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, beforeAll } from "vitest";
import { tokens, applyTheme, THEMES } from "../tokens";
import {
  LINK_ISL_COLOR, LINK_GROUND_COLOR, LINK_FAIL_COLOR,
  LINK_INACTIVE_COLOR, LINK_FLOW_COLOR, LINK_FLOW_SECONDARY_COLOR,
  LINK_ISL_WIDTH, LINK_GROUND_WIDTH, LINK_FLOW_WIDTH,
  GS_COLOR, SELECTION_COLOR, FAIL_HOLD_MS, FAIL_FADE_MS,
  EARTH_RADIUS, SAT_RADIUS, SAT_SEGMENTS, GS_SIZE,
  CAMERA_FOV, CAMERA_DISTANCE, CAMERA_MIN_DISTANCE, CAMERA_MAX_DISTANCE,
  AREA_COLORS, PLANE_COLORS, getPlaneColor,
} from "../../config";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore -- Node built-ins available at vitest runtime
import { readFileSync, readdirSync } from "node:fs";
// @ts-ignore
import { resolve, dirname } from "node:path";
// @ts-ignore
import { fileURLToPath } from "node:url";

beforeAll(() => {
  applyTheme();
});

describe("token system", () => {
  describe("config.ts re-exports match tokens.ts source values", () => {
    it("link colors match their token source", () => {
      expect(LINK_ISL_COLOR).toBe(tokens.colorLinkIsl);
      expect(LINK_GROUND_COLOR).toBe(tokens.colorLinkGround);
      expect(LINK_FAIL_COLOR).toBe(tokens.colorLinkFail);
      expect(LINK_INACTIVE_COLOR).toBe(tokens.colorLinkInactive);
      expect(LINK_FLOW_COLOR).toBe(tokens.colorLinkFlow);
      expect(LINK_FLOW_SECONDARY_COLOR).toBe(tokens.colorLinkFlowSecondary);
    });

    it("link widths match their token source", () => {
      expect(LINK_ISL_WIDTH).toBe(tokens.linkWidthIsl);
      expect(LINK_GROUND_WIDTH).toBe(tokens.linkWidthGround);
      expect(LINK_FLOW_WIDTH).toBe(tokens.linkWidthFlow);
    });

    it("node colors match their token source", () => {
      expect(GS_COLOR).toBe(tokens.colorNodeGs);
      expect(SELECTION_COLOR).toBe(tokens.colorNodeSelected);
    });

    it("timing values match their token source", () => {
      expect(FAIL_HOLD_MS).toBe(tokens.failHoldMs);
      expect(FAIL_FADE_MS).toBe(tokens.failFadeMs);
    });

    it("scene constants match their token source", () => {
      expect(EARTH_RADIUS).toBe(tokens.earthRadius);
      expect(SAT_RADIUS).toBe(tokens.satRadius);
      expect(SAT_SEGMENTS).toBe(tokens.satSegments);
      expect(GS_SIZE).toBe(tokens.gsSize);
      expect(CAMERA_FOV).toBe(tokens.cameraFov);
      expect(CAMERA_DISTANCE).toBe(tokens.cameraDistance);
      expect(CAMERA_MIN_DISTANCE).toBe(tokens.cameraMinDistance);
      expect(CAMERA_MAX_DISTANCE).toBe(tokens.cameraMaxDistance);
    });

    it("area colors use token values", () => {
      expect(AREA_COLORS["49.0001"]).toBe(tokens.areaRed);
      expect(AREA_COLORS["49.0002"]).toBe(tokens.areaGreen);
      expect(AREA_COLORS["49.0003"]).toBe(tokens.areaBlue);
      expect(AREA_COLORS["49.0004"]).toBe(tokens.areaAmber);
    });

    it("plane colors array is the same reference as tokens", () => {
      expect(PLANE_COLORS).toBe(tokens.planeColors);
    });
  });

  describe("CSS injection produces correct values (not just non-empty)", () => {
    const stringMappings: [string, string][] = [
      ["--bg-main", tokens.bgMain],
      ["--bg-panel", tokens.bgPanel],
      ["--text-primary", tokens.textPrimary],
      ["--text-secondary", tokens.textSecondary],
      ["--text-dim", tokens.textDim],
      ["--border", tokens.border],
      ["--border-strong", tokens.borderStrong],
      ["--accent-blue", tokens.accentBlue],
      ["--accent-red", tokens.accentRed],
      ["--accent-green", tokens.accentGreen],
      ["--status-ok", tokens.statusOk],
      ["--status-warn", tokens.statusWarn],
      ["--status-fail", tokens.statusFail],
      ["--font-mono", tokens.fontFamilyCli],
      ["--font-ui", tokens.fontFamilyUi],
      ["--font-size-xs", tokens.fontSizeXs],
      ["--font-size-md", tokens.fontSizeMd],
      ["--topbar-height", tokens.topbarHeight],
      ["--panel-width", tokens.panelWidth],
      ["--radius-md", tokens.radiusMd],
      ["--transition-fast", tokens.transitionFast],
      ["--space-4", tokens.space4],
      ["--space-8", tokens.space8],
    ];

    it.each(stringMappings)(
      "%s CSS value matches token source string",
      (cssVar, tokenVal) => {
        const cssVal = document.documentElement.style.getPropertyValue(cssVar);
        expect(cssVal).toBe(tokenVal);
      },
    );

    const numericMappings: [string, number][] = [
      ["--z-panel", tokens.zPanel],
      ["--z-overlay", tokens.zOverlay],
      ["--z-tooltip", tokens.zTooltip],
      ["--z-window", tokens.zWindow],
      ["--font-weight-semibold", tokens.fontWeightSemibold],
    ];

    it.each(numericMappings)(
      "%s CSS value matches token numeric value",
      (cssVar, tokenVal) => {
        const cssVal = document.documentElement.style.getPropertyValue(cssVar);
        expect(cssVal).toBe(String(tokenVal));
      },
    );
  });

  describe("CSS files only reference variables that are injected", () => {
    it("every var() reference in CSS files resolves to an injected property", () => {
      const thisDir = dirname(fileURLToPath(import.meta.url));
      const stylesDir = resolve(thisDir, "..");
      const cssFiles = (readdirSync(stylesDir) as string[]).filter((f) => f.endsWith(".css"));

      const style = document.documentElement.style;
      const injectedVars = new Set<string>();
      for (let i = 0; i < style.length; i++) {
        const prop = style.item(i);
        if (prop) injectedVars.add(prop);
      }

      expect(injectedVars.size).toBeGreaterThan(40);

      const missingVars: string[] = [];

      for (const file of cssFiles as string[]) {
        const content = readFileSync(resolve(stylesDir, file), "utf-8") as string;
        const varRefs = content.match(/var\(--[\w-]+/g) ?? [];
        const uniqueRefs = [...new Set(varRefs.map((r: string) => r.replace("var(", "")))];

        for (const varName of uniqueRefs) {
          if (!injectedVars.has(varName)) {
            missingVars.push(`${file}: var(${varName})`);
          }
        }
      }

      expect(
        missingVars,
        `CSS files reference ${missingVars.length} var() names not injected by applyTheme():\n` +
        missingVars.join("\n") +
        "\nThese will silently produce no styling.",
      ).toHaveLength(0);
    });
  });

  describe("domain invariants", () => {
    it("z-index layers form a strict total order", () => {
      const layers = [
        tokens.zPanel,
        tokens.zCliDrawer,
        tokens.zPopover,
        tokens.zCatalog,
        tokens.zOverlay,
        tokens.zScrim,
        tokens.zToast,
        tokens.zTooltip,
        tokens.zWindow,
      ];
      for (let i = 1; i < layers.length; i++) {
        expect(
          layers[i]!,
          `z-index layer ${i} (${layers[i]}) must be > layer ${i - 1} (${layers[i - 1]})`,
        ).toBeGreaterThan(layers[i - 1]!);
      }
    });

    it("link widths are ordered: ISL < ground < flow", () => {
      expect(tokens.linkWidthIsl).toBeLessThan(tokens.linkWidthGround);
      expect(tokens.linkWidthGround).toBeLessThan(tokens.linkWidthFlow);
    });

    it("satellite radius is smaller than earth (scene proportions)", () => {
      expect(tokens.satRadius).toBeLessThan(tokens.earthRadius);
      expect(tokens.satRadius / tokens.earthRadius).toBeLessThan(0.1);
    });

    it("camera distance is outside the earth", () => {
      expect(tokens.cameraDistance).toBeGreaterThan(tokens.earthRadius);
      expect(tokens.cameraMinDistance).toBeGreaterThan(tokens.earthRadius);
      expect(tokens.cameraMaxDistance).toBeGreaterThan(tokens.cameraDistance);
    });

    it("fail-flash hold is longer than a single frame", () => {
      expect(tokens.failHoldMs).toBeGreaterThan(16);
      expect(tokens.failFadeMs).toBeGreaterThan(16);
    });

    it("plane colors are all distinct", () => {
      const unique = new Set(tokens.planeColors);
      expect(unique.size).toBe(tokens.planeColors.length);
    });

    it("all Three.js hex colors are valid 24-bit values", () => {
      const hexKeys = Object.entries(tokens).filter(
        ([k, v]) => typeof v === "number" && (k.startsWith("color") || k.startsWith("area")),
      );
      expect(hexKeys.length).toBeGreaterThanOrEqual(10);
      for (const [key, value] of hexKeys) {
        expect(value, `${key} is out of 24-bit range`).toBeGreaterThanOrEqual(0);
        expect(value, `${key} is out of 24-bit range`).toBeLessThanOrEqual(0xffffff);
        expect(Number.isInteger(value), `${key} is not an integer`).toBe(true);
      }
    });
  });

  describe("getPlaneColor respects token source", () => {
    it("returns token color for first cycle", () => {
      for (let i = 0; i < tokens.planeColors.length; i++) {
        expect(getPlaneColor(i)).toBe(tokens.planeColors[i]);
      }
    });

    it("returns darkened variant for second cycle", () => {
      const base = tokens.planeColors[0]!;
      const darkened = getPlaneColor(tokens.planeColors.length);
      expect(darkened).not.toBe(base);
      const baseR = (base >> 16) & 0xff;
      const darkR = (darkened >> 16) & 0xff;
      expect(darkR).toBeLessThan(baseR);
    });
  });

  describe("applyTheme idempotency", () => {
    it("calling twice produces the same values", () => {
      const before = document.documentElement.style.getPropertyValue("--accent-blue");
      applyTheme();
      const after = document.documentElement.style.getPropertyValue("--accent-blue");
      expect(after).toBe(before);
    });
  });

  describe("theme structure", () => {
    it("both themes define identical key sets", () => {
      const names = Object.keys(THEMES) as (keyof typeof THEMES)[];
      expect(names.length).toBe(2);
      const keySets = names.map((n) => Object.keys(THEMES[n]).sort().join(","));
      expect(keySets[0]).toBe(keySets[1]);
    });

    it("theme color strings are 6-digit hex (withAlpha and Three.js parse them)", () => {
      for (const [themeName, theme] of Object.entries(THEMES)) {
        for (const [key, value] of Object.entries(theme)) {
          if (typeof value !== "string" || !value.startsWith("#")) continue;
          expect(value, `${themeName}.${key}`).toMatch(/^#[0-9a-fA-F]{6}$/);
        }
      }
    });
  });
});
