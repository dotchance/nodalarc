import { defineConfig } from "vite";

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

  return {
    envPrefix: "VITE_",
    define: {
      __BUILD_HASH__: JSON.stringify(buildHash),
      __BUILD_TIME__: JSON.stringify(new Date().toISOString().substring(0, 19)),
    },
    build: {
      outDir: "dist",
      sourcemap: true,
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
