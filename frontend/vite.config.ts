import { defineConfig } from "vite";
import { execSync } from "child_process";

let commitHash = process.env.VITE_BUILD_HASH || "dev";
try {
  commitHash = execSync("git rev-parse --short=8 HEAD").toString().trim();
} catch { /* not in git — use VITE_BUILD_HASH from Docker ARG */ }

export default defineConfig({
  envPrefix: "VITE_",
  define: {
    __BUILD_HASH__: JSON.stringify(commitHash),
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
  },
});
