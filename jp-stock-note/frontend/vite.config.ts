import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to our own server (same-origin /api/*).
// In `npm run dev`, /api is proxied to the local FastAPI process.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
});
