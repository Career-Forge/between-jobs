import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { assertApiBase } from "./scripts/apiBaseGuard.ts";

// Fails `vite build` unless VITE_API_BASE_URL is a usable production value
// (scripts/apiBaseGuard.ts says what counts and why). Reads the RESOLVED env, the
// same values Vite is about to inline into the bundle -- shell variables over
// .env.local over .env -- so what is checked is what ships. A dev server or
// vitest run is a different command and is never stopped by it.
function requireApiBaseForBuilds(): Plugin {
  return {
    name: "between-jobs:require-api-base",
    configResolved(config) {
      if (config.command === "build") {
        assertApiBase(config.env.VITE_API_BASE_URL as string | undefined);
      }
    },
  };
}

// The web client is strictly a thin client over the FastAPI spine
// (Proposal §3: channels are renderers). In dev, /api/* proxies to the
// spine so the browser never deals with CORS; the /api prefix is
// stripped because the spine's routes are mounted at the root
// (/profile/..., /sessions, /health). A deployed build has no proxy: the API is a
// different origin, named by VITE_API_BASE_URL (src/lib/apiUrl.ts).
export default defineConfig({
  plugins: [react(), requireApiBaseForBuilds()],
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
