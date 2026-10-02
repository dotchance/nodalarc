// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
import { describe, it, expect, beforeAll } from "vitest";
import { tokens, applyTheme, THEMES } from "../tokens";
import { getPlaneColor } from "../../config";
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
  describe("TSX/TS inline styles only reference variables that are injected", () => {
    it("every var() reference in source files resolves to an injected property", () => {
      const thisDir = dirname(fileURLToPath(import.meta.url));
      const srcDir = resolve(thisDir, "..", "..");

      const style = document.documentElement.style;
      const injectedVars = new Set<string>();
      for (let i = 0; i < style.length; i++) {
        const prop = style.item(i);
        if (prop) injectedVars.add(prop);
      }

      const missing: string[] = [];
      const walk = (dir: string) => {
        for (const entry of readdirSync(dir, { withFileTypes: true }) as {
          name: string;
          isDirectory(): boolean;
        }[]) {
          const full = resolve(dir, entry.name);
          if (entry.isDirectory()) {
            if (entry.name === "node_modules" || entry.name === "__tests__") continue;
            walk(full);
            continue;
          }
          if (!entry.name.endsWith(".tsx") && !entry.name.endsWith(".ts")) continue;
          if (full.endsWith("styles/tokens.ts")) continue; // definition site; comments mention var(--token-name)
          const content = readFileSync(full, "utf-8") as string;
          const refs = content.match(/var\(--[\w-]+/g) ?? [];
          for (const ref of new Set(refs.map((r: string) => r.replace("var(", "")))) {
            // Custom properties set locally by components (not theme tokens).
            if (ref === "--panel-width" || ref === "--relation-color" || ref === "--medium-color" || ref === "--state-color") continue;
            if (!injectedVars.has(ref)) {
              missing.push(`${full.replace(srcDir, "src")}: var(${ref})`);
            }
          }
        }
      };
      walk(srcDir);

      expect(
        missing,
        `Source files reference ${missing.length} var() names not injected by applyTheme():\n` +
          missing.join("\n") +
          "\nInline var() with no injected value silently renders nothing — the --font-mono failure class.",
      ).toHaveLength(0);
    });
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

  describe("stylesheets carry no raw colors", () => {
    it("every color in src CSS comes from a token or color-mix, never a literal", () => {
      const thisDir = dirname(fileURLToPath(import.meta.url));
      const srcDir = resolve(thisDir, "..", "..");
      const offenders: string[] = [];
      const walk = (dir: string) => {
        for (const entry of readdirSync(dir, { withFileTypes: true }) as {
          name: string;
          isDirectory(): boolean;
        }[]) {
          const full = resolve(dir, entry.name);
          if (entry.isDirectory()) {
            if (entry.name === "node_modules") continue;
            walk(full);
            continue;
          }
          if (!entry.name.endsWith(".css")) continue;
          const content = readFileSync(full, "utf-8") as string;
          for (const [lineIdx, line] of content.split("\n").entries()) {
            if (/#[0-9a-fA-F]{3,8}\b/.test(line) || /\brgba?\(/.test(line) || /\bhsla?\(/.test(line)) {
              offenders.push(`${full.replace(srcDir, "src")}:${lineIdx + 1}: ${line.trim()}`);
            }
          }
        }
      };
      walk(srcDir);
      expect(
        offenders,
        "Raw color literals in stylesheets bypass theming:\n" + offenders.join("\n"),
      ).toHaveLength(0);
    });
  });

  describe("getPlaneColor respects token source", () => {
    it("returns darkened variant for second cycle", () => {
      const base = tokens.planeColors[0]!;
      const darkened = getPlaneColor(tokens.planeColors.length);
      expect(darkened).not.toBe(base);
      const baseR = (base >> 16) & 0xff;
      const darkR = (darkened >> 16) & 0xff;
      expect(darkR).toBeLessThan(baseR);
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
