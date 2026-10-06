import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
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
// (/profile/..., /applications/..., /health). A deployed build has no proxy: the API is a
// different origin, named by VITE_API_BASE_URL (src/lib/apiUrl.ts).
export default defineConfig({
  plugins: [react(), requireApiBaseForBuilds()],
  test: {
    // Vitest turns every stylesheet into an empty string unless it is listed here, `?raw` imports
    // included. app.css is read as text by src/lib/publicPagesStyles.test.ts (there is no DOM, so
    // no computed style): the rules that keep prose links underlined and the footer aligned.
    css: { include: [/src\/app\.css/] },
    // src/lib/supabase.ts throws at import without these, and several components reach it
    // through api.ts. Placeholders, so `npm test` passes on a clean clone and in CI
    // instead of only on a machine that has a .env.local. Nothing in the suite connects.
    env: {
      VITE_SUPABASE_URL: "http://localhost:54321",
      VITE_SUPABASE_PUBLISHABLE_KEY: "test-publishable-key",
    },
  },
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
