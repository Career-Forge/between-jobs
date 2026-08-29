import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The web client is strictly a thin client over the FastAPI spine
// (Proposal §3: channels are renderers). In dev, /api/* proxies to the
// spine so the browser never deals with CORS; the /api prefix is
// stripped because the spine's routes are mounted at the root
// (/profile/..., /sessions, /health).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8012",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
