import react from "@vitejs/plugin-react";
import type { Plugin, ResolvedConfig } from "vite";
import { defineConfig } from "vitest/config";
import { assertApiBase } from "./scripts/apiBaseGuard.ts";
import { assetsPrefixFor, buildHeadersFile, resolveOrigins } from "./scripts/securityHeaders.ts";

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

// Adds the security headers file to every `vite build` (scripts/securityHeaders.ts holds the policy
// and the reasoning). A plugin rather than a step after `vite build` in package.json, because it
// reads the same resolved env Vite inlines into the bundle (so the policy cannot name different
// hosts than the code calls), honours --outDir and --mode, and reads the HTML Vite actually emitted,
// whose inline <style> it hashes. The file is emitted as part of the bundle, so it lands in the
// output directory and nowhere else, and a build that fails writes nothing.
//
// --base (and build.assetsDir) must be a path of plain names on the site's own origin, such as "/" or
// "/app/": the cache rule is that path on this host, and the policy allows scripts and styles from
// 'self' only. An absolute or protocol-relative base (a CDN) is refused rather than built into a page
// the policy would blank.
//
// Only `vite build` gets it. The dev server needs no policy, and `vite preview` serves the built
// files WITHOUT applying `_headers` (only Cloudflare Pages, or a server you configure from it, does),
// so a CSP problem cannot show up there: test the built output behind a server that applies the file.
//
// The build log prints the whole file at info level, which is how a self-hoster on another host
// copies the policy out; `vite build --logLevel warn` silences it.
function emitSecurityHeaders(): Plugin {
  let config: ResolvedConfig;
  let assetsPrefix: string;
  return {
    name: "between-jobs:security-headers",
    apply: "build",
    configResolved(resolved) {
      config = resolved;
      // Fail now, before bundling, when either origin or the asset path is unusable.
      resolveOrigins(resolved.env);
      assetsPrefix = assetsPrefixFor(resolved.base, resolved.build.assetsDir);
    },
    generateBundle: {
      // After vite:build-html has emitted index.html.
      order: "post",
      async handler(_options, bundle) {
        const decoder = new TextDecoder();
        const htmlDocuments = Object.values(bundle).flatMap((output) => {
          if (output.type !== "asset" || !output.fileName.endsWith(".html")) return [];
          return [typeof output.source === "string" ? output.source : decoder.decode(output.source)];
        });
        const source = await buildHeadersFile({
          env: config.env,
          htmlDocuments,
          assetsPrefix,
        });
        this.emitFile({ type: "asset", fileName: "_headers", source });
        config.logger.info(`\nWriting _headers to the output directory:\n\n${source}`);
      },
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
  plugins: [react(), requireApiBaseForBuilds(), emitSecurityHeaders()],
  build: {
    // Never turn a small image or font into a data: URL inside the bundle: the policy allows
    // img-src and font-src from 'self' only, so an inlined asset would be refused in production
    // and work everywhere else. Files under assets/ are same-origin and cached for a year.
    assetsInlineLimit: 0,
  },
  test: {
    // Vitest turns every stylesheet into an empty string unless it is listed here, `?raw` imports
    // included. app.css is read as text by src/lib/publicPagesStyles.test.ts (there is no DOM, so
    // no computed style): the rules that keep prose links underlined and the footer aligned. The
    // tokens and fonts sheets are read the same way, by the tests that pin the design tokens and
    // the self-hosted font files.
    css: { include: [/src\/(?:app|tokens|fonts)\.css/] },
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
