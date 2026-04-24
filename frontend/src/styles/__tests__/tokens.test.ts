// Copyright 2024-2026 .chance (dotchance)
// Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
import { describe, it, expect, beforeAll } from "vitest";
import { tokens, injectCssTokens } from "../tokens";

beforeAll(() => {
  injectCssTokens();
});

describe("tokens.ts", () => {
  describe("Three.js hex color validity", () => {
    const hexColorKeys = Object.entries(tokens).filter(
      ([k, v]) => typeof v === "number" && k.startsWith("color"),
    );

    it("defines at least 5 node/link color tokens", () => {
      expect(hexColorKeys.length).toBeGreaterThanOrEqual(5);
    });

    it.each(hexColorKeys)("%s is a valid 24-bit hex color", (_key, value) => {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(0xffffff);
      expect(Number.isInteger(value)).toBe(true);
    });
  });

  describe("plane colors", () => {
    it("has at least 6 distinct plane colors", () => {
      expect(tokens.planeColors.length).toBeGreaterThanOrEqual(6);
      const unique = new Set(tokens.planeColors);
      expect(unique.size).toBe(tokens.planeColors.length);
    });

    it("all plane colors are valid 24-bit hex", () => {
      for (const c of tokens.planeColors) {
        expect(c).toBeGreaterThanOrEqual(0);
        expect(c).toBeLessThanOrEqual(0xffffff);
      }
    });
  });

  describe("CSS injection completeness", () => {
    const style = document.documentElement.style;

    const requiredCssVars = [
      "--bg-main",
      "--bg-panel",
      "--bg-panel-hover",
      "--bg-toolbar",
      "--bg-bar",
      "--text-primary",
      "--text-secondary",
      "--text-dim",
      "--border",
      "--accent-blue",
      "--accent-teal",
      "--accent-orange",
      "--accent-red",
      "--accent-green",
      "--accent-amber",
      "--color-node-satellite",
      "--color-node-gs",
      "--color-link-isl",
      "--color-link-ground",
      "--color-link-fail",
      "--ws-connected",
      "--ws-reconnecting",
      "--ws-disconnected",
      "--font-family",
      "--font-size-xs",
      "--font-size-sm",
      "--font-size-md",
      "--font-size-lg",
      "--font-size-xl",
      "--z-panel",
      "--z-overlay",
      "--z-tooltip",
      "--radius-sm",
      "--radius-md",
      "--radius-lg",
      "--radius-xl",
      "--topbar-height",
      "--bottombar-height",
      "--panel-width",
      "--transition-fast",
      "--transition-normal",
      "--space-2",
      "--space-4",
      "--space-6",
      "--space-8",
    ];

    it.each(requiredCssVars)(
      "injects %s into document.documentElement",
      (varName) => {
        const val = style.getPropertyValue(varName);
        expect(val, `${varName} was not injected`).not.toBe("");
      },
    );
  });

  describe("token value types", () => {
    it("scene constants are numeric", () => {
      expect(typeof tokens.earthRadius).toBe("number");
      expect(typeof tokens.satRadius).toBe("number");
      expect(typeof tokens.cameraFov).toBe("number");
      expect(tokens.earthRadius).toBeGreaterThan(0);
      expect(tokens.satRadius).toBeGreaterThan(0);
      expect(tokens.satRadius).toBeLessThan(tokens.earthRadius);
    });

    it("z-index layers are ordered correctly", () => {
      expect(tokens.zPanel).toBeLessThan(tokens.zCliDrawer);
      expect(tokens.zCliDrawer).toBeLessThan(tokens.zPopover);
      expect(tokens.zPopover).toBeLessThan(tokens.zCatalog);
      expect(tokens.zCatalog).toBeLessThan(tokens.zOverlay);
      expect(tokens.zOverlay).toBeLessThan(tokens.zScrim);
      expect(tokens.zScrim).toBeLessThan(tokens.zToast);
      expect(tokens.zToast).toBeLessThan(tokens.zTooltip);
    });

    it("fail-flash timing is positive", () => {
      expect(tokens.failHoldMs).toBeGreaterThan(0);
      expect(tokens.failFadeMs).toBeGreaterThan(0);
    });

    it("link widths are ordered: ISL < ground < flow", () => {
      expect(tokens.linkWidthIsl).toBeLessThan(tokens.linkWidthGround);
      expect(tokens.linkWidthGround).toBeLessThan(tokens.linkWidthFlow);
    });
  });

  describe("CSS-Three.js color consistency", () => {
    it("GS color token matches CSS injection", () => {
      const cssVal = document.documentElement.style.getPropertyValue("--color-node-gs");
      const expectedHex = "#" + tokens.colorNodeGs.toString(16).padStart(6, "0");
      expect(cssVal).toBe(expectedHex);
    });

    it("ISL link color token matches CSS injection", () => {
      const cssVal = document.documentElement.style.getPropertyValue("--color-link-isl");
      const expectedHex = "#" + tokens.colorLinkIsl.toString(16).padStart(6, "0");
      expect(cssVal).toBe(expectedHex);
    });
  });
});
