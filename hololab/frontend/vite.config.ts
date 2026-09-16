import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the gateway lives on :8828 and this dev server on :5173. We proxy
// /api, /ws, and /proxy so the frontend can pretend it is same-origin.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8828",
      "/proxy": "http://127.0.0.1:8828",
      "/ws": {
        target: "ws://127.0.0.1:8828",
        ws: true,
      },
    },
  },
});
