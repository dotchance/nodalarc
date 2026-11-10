// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
//
// The page serves its legal notices beside its bundle, under legal/:
// NodalArc's LICENSE and NOTICE, the repository's third-party notices (fonts,
// icons, textures, map data), and third-party-licenses.txt, the license text of
// every npm package the bundle holds. Every browser that loads the page
// receives a copy of those packages, and their licenses ask that their notices
// go with each copy, so the About section links all four files.
//
// The package list comes from the build's own module graph, not from the lock
// file's dev flags: a package the code imports lands in the bundle whichever
// dependency list names it, so it lands in the notices too. A package that
// ships no license file needs its text in LICENSES/npm/<name>@<version>.txt
// (a scoped name's "/" written as "+"); the file is tied to the version, so a
// version change fails the build until someone confirms the text again. A
// bundled package with neither fails the build.

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";

const NODE_MODULES = "/node_modules/";
const LICENSE_FILE = /^(licen[cs]e|copying)([.-].*)?$/i;
// The repository files the page serves, read from the repository root (the
// frontend directory's parent; the page image's build stage places them there).
const SERVED_REPOSITORY_FILES: Array<[string, string]> = [
  ["LICENSE", "legal/LICENSE.txt"],
  ["NOTICE", "legal/NOTICE.txt"],
  ["THIRD_PARTY_NOTICES.md", "legal/THIRD_PARTY_NOTICES.txt"],
];
export const THIRD_PARTY_LICENSES_FILE = "legal/third-party-licenses.txt";

function packageRoot(moduleId: string): string | null {
  const file = moduleId.split("?")[0]!.replaceAll("\\", "/");
  const at = file.lastIndexOf(NODE_MODULES);
  if (at < 0) return null;
  const rest = file.slice(at + NODE_MODULES.length).split("/");
  const name = rest[0]!.startsWith("@") ? `${rest[0]}/${rest[1]}` : rest[0]!;
  return file.slice(0, at + NODE_MODULES.length) + name;
}

/**
 * Two plugins sharing one record: `collect` runs in the page build and in every
 * worker build and records each package root the module graph holds; `emit`
 * runs in the page build, after the worker builds, and writes legal/.
 */
export function legalNotices(): { collect: () => Plugin; emit: Plugin } {
  const roots = new Set<string>();
  let repositoryRoot = "";

  const collect = (): Plugin => ({
    name: "nodalarc-legal-notices-collect",
    apply: "build",
    generateBundle() {
      for (const id of this.getModuleIds()) {
        const root = packageRoot(id);
        if (root) roots.add(root);
      }
    },
  });

  const emit: Plugin = {
    name: "nodalarc-legal-notices-emit",
    apply: "build",
    configResolved(config) {
      repositoryRoot = path.resolve(config.root, "..");
    },
    generateBundle() {
      for (const [source, served] of SERVED_REPOSITORY_FILES) {
        this.emitFile({
          type: "asset",
          fileName: served,
          source: readFileSync(path.join(repositoryRoot, source), "utf-8"),
        });
      }
      const entries: string[] = [];
      const byName = new Map<string, { version: string; license: string; text: string }>();
      for (const root of roots) {
        const manifest = JSON.parse(readFileSync(path.join(root, "package.json"), "utf-8")) as {
          name: string;
          version: string;
          license?: string | { type?: string };
        };
        const license =
          typeof manifest.license === "string" ? manifest.license : (manifest.license?.type ?? "");
        const shipped = readdirSync(root)
          .filter((file) => LICENSE_FILE.test(file))
          .sort()
          .map((file) => readFileSync(path.join(root, file), "utf-8").trim());
        let text = shipped.join("\n\n");
        if (!text) {
          const supplied = path.join(
            repositoryRoot,
            "LICENSES",
            "npm",
            `${manifest.name.replace("/", "+")}@${manifest.version}.txt`,
          );
          try {
            text = readFileSync(supplied, "utf-8").trim();
          } catch {
            this.error(
              `${manifest.name}@${manifest.version} is in the bundle and ships no license file; ` +
                `add its license text as ${path.relative(repositoryRoot, supplied)}`,
            );
          }
        }
        if (!license) {
          this.error(`${manifest.name}@${manifest.version} is in the bundle and names no license`);
        }
        const known = byName.get(manifest.name);
        if (known && known.version !== manifest.version) {
          this.error(
            `${manifest.name} is in the bundle at ${known.version} and ${manifest.version}; ` +
              "the notices name one version per package",
          );
        }
        byName.set(manifest.name, { version: manifest.version, license, text });
      }
      for (const name of [...byName.keys()].sort()) {
        const { version, license, text } = byName.get(name)!;
        entries.push(`==== ${name} ${version} (${license}) ====\n\n${text}\n`);
      }
      this.emitFile({
        type: "asset",
        fileName: THIRD_PARTY_LICENSES_FILE,
        source:
          "Third-party software in the NodalArc web page\n\n" +
          "The page bundles the npm packages below. Each entry names the package, its version\n" +
          "and its license, followed by the license text the package ships.\n\n" +
          entries.join("\n"),
      });
    },
  };

  return { collect, emit };
}
