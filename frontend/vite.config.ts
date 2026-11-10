// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
import { defineConfig } from "vite";
import { legalNotices } from "./legalNotices";

function requiredBuildHash(command: string): string {
  const buildHash = process.env.VITE_BUILD_HASH?.trim();
  if (!buildHash) {
    throw new Error("VITE_BUILD_HASH is required; run through make or set it explicitly.");
  }
  if (command === "build" && /^(dev|test|preview|unknown)$/i.test(buildHash)) {
    throw new Error("VITE_BUILD_HASH must be a real build hash for production builds.");
  }
  return buildHash;
}

export default defineConfig(({ command }) => {
  const buildHash = requiredBuildHash(command);

  // Dev-only: when VITE_DEV_PROXY_TARGET is set (e.g. http://192.168.10.132:8080),
  // the dev server proxies /api and /ws to a live VS-API so the frontend can iterate against a
  // running session with full HMR — no container rebuild. Same-origin via the proxy, so no
  // CORS. Has no effect on production builds (which never use `server`) or normal dev.
  const devProxyTarget = process.env.VITE_DEV_PROXY_TARGET?.trim();
  const proxy = devProxyTarget
    ? {
        "/api": { target: devProxyTarget, changeOrigin: true },
        "/ws": { target: devProxyTarget.replace(/^http/, "ws"), ws: true, changeOrigin: true },
      }
    : undefined;

  const notices = legalNotices();

  return {
    envPrefix: "VITE_",
    plugins: [notices.collect(), notices.emit],
    worker: {
      plugins: () => [notices.collect()],
    },
    define: {
      __BUILD_HASH__: JSON.stringify(buildHash),
      __BUILD_TIME__: JSON.stringify(new Date().toISOString().substring(0, 19)),
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
    test: {
      environment: "jsdom",
    },
    server: {
      port: 3000,
      host: "0.0.0.0",
      proxy,
    },
  };
});
