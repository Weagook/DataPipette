import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "canvas-runtime",
              test: /node_modules[\\/](@xyflow|zustand|classcat|d3-|internmap|use-sync-external-store)/,
              includeDependenciesRecursively: false,
              priority: 20,
            },
          ],
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
    maxWorkers: 1,
    pool: "vmThreads",
    testTimeout: 20_000,
  },
});
