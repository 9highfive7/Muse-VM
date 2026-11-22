import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Static demo build (GitHub Pages): `npm run build:demo` (= `vite build --mode demo`;
// VITE_DEMO=1 works too). Only then are src/main.tsx and src/router.tsx swapped
// for their src/demo/ counterparts, which answer /api/* from recorded synthetic
// responses and route via the URL hash. The normal build is untouched by this.
const SRC = fileURLToPath(new URL("./src/", import.meta.url));
const DEMO_SWAPS: Record<string, string> = {
  [`${SRC}main.tsx`]: `${SRC}demo/main.tsx`,
  [`${SRC}router.tsx`]: `${SRC}demo/router.tsx`,
};

function demoSwap(): Plugin {
  return {
    name: "stockapp-demo-swap",
    enforce: "pre",
    async resolveId(source, importer, options) {
      const resolved = await this.resolve(source, importer, { ...options, skipSelf: true });
      const swap = resolved && DEMO_SWAPS[resolved.id];
      return swap ? swap : null;
    },
  };
}

export default defineConfig(({ mode }) => {
  const demo = mode === "demo" || process.env.VITE_DEMO === "1";
  if (demo) {
    return {
      plugins: [demoSwap(), react()],
      base: "./",
      build: { outDir: "dist-demo", emptyOutDir: true, sourcemap: false },
    };
  }
  // The browser only ever talks to our own server (same-origin /api/*).
  // In `npm run dev`, /api is proxied to the local FastAPI process.
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      proxy: { "/api": "http://127.0.0.1:8000" },
    },
    build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
  };
});
