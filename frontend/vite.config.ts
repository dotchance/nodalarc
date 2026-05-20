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
    },
  };
});
