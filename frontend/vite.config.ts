import { defineConfig } from "vite";

export default defineConfig({
  envPrefix: "VITE_",
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  server: {
    port: 3000,
    host: "0.0.0.0",
  },
});
