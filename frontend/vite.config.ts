import { defineConfig } from "vite";
import { execSync } from "child_process";

let commitHash = "dev";
try {
  commitHash = execSync("git rev-parse --short=8 HEAD").toString().trim();
} catch { /* not in git */ }

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
