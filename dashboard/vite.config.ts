import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Performance budgets for the ECDAT frontend (Phase 8, Sep 2026).
const FRONTEND_BUDGETS = {
  maxBundleGzipKb: 500,
  // ImpactX baseline after the evidence, history, and responsive assurance views.
  // Keep narrow headroom so a material regression still fails the build.
  maxTotalJsKb: 810,
  maxCssKb: 155,
};

export default defineConfig(({ mode }) => ({
  // Keep Vitest from invalidating the dependency graph used by the live dev
  // server. Otherwise lazy chunks can fail with `504 Outdated Optimize Dep`.
  cacheDir: mode === "test" ? "node_modules/.vitest-cache" : "node_modules/.vite-cache",
  plugins: [
    react(),
    {
      name: "performance-budget",
      async closeBundle() {
        const fs = await import("fs");
        const path = await import("path");
        const zlib = await import("zlib");

        const assetsDir = path.resolve("dist/assets");
        if (!fs.existsSync(assetsDir)) return;

        let jsTotal = 0;
        let cssTotal = 0;
        let gzipTotal = 0;

        for (const entry of fs.readdirSync(assetsDir)) {
          const filePath = path.join(assetsDir, entry);
          if (!entry.endsWith(".js") && !entry.endsWith(".css")) continue;
          const contents = fs.readFileSync(filePath);
          gzipTotal += zlib.gzipSync(contents).length;
          if (entry.endsWith(".js")) jsTotal += contents.length;
          if (entry.endsWith(".css")) cssTotal += contents.length;
        }

        const jsKb = jsTotal / 1024;
        const cssKb = cssTotal / 1024;
        const gzipKb = gzipTotal / 1024;

        const warnings: string[] = [];
        if (jsKb > FRONTEND_BUDGETS.maxTotalJsKb) {
          warnings.push(
            `[perf-budget] Total JS ${jsKb.toFixed(1)}KB exceeds budget of ${FRONTEND_BUDGETS.maxTotalJsKb}KB`,
          );
        }
        if (cssKb > FRONTEND_BUDGETS.maxCssKb) {
          warnings.push(
            `[perf-budget] Total CSS ${cssKb.toFixed(1)}KB exceeds budget of ${FRONTEND_BUDGETS.maxCssKb}KB`,
          );
        }
        if (gzipKb > FRONTEND_BUDGETS.maxBundleGzipKb) {
          warnings.push(
            `[perf-budget] Gzipped bundle ${gzipKb.toFixed(1)}KB exceeds budget of ${FRONTEND_BUDGETS.maxBundleGzipKb}KB`,
          );
        }

        if (warnings.length) throw new Error(warnings.join("\n"));
      },
    },
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id: string) => {
          if (
            id.includes("node_modules/react") ||
            id.includes("node_modules/react-dom") ||
            id.includes("node_modules/react-router")
          )
            return "react";
          if (id.includes("node_modules/recharts")) return "charts";
          if (id.includes("node_modules/framer-motion")) return "motion";
          return undefined;
        },
      },
    },
    reportCompressedSize: true,
    chunkSizeWarningLimit: 500,
  },
  server: {
    host: "0.0.0.0",
    port: 3000,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
}));
