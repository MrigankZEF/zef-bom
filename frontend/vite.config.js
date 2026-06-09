import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api → FastAPI so the frontend can use same-origin relative URLs.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Backend serves the API under /api, so forward as-is (no rewrite).
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
