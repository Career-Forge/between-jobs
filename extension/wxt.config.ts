import path from "node:path";
import { defineConfig } from "wxt";
import { assertStoreBuild } from "./scripts/store-build-guard";

// browser-extension.md E2 -- Lever only originally (D1: prove the engine
// deeply on one ATS before touching Greenhouse/Ashby), Chrome only (D2:
// chrome.sidePanel has no compatible equivalent in Firefox/Safari).
//
// Permissions -- each one is used, and nothing else is requested (Proposal
// §29.2's own discipline: "do not ask for access to every website without a
// clear feature reason", and the Chrome Web Store rejects requested-but-
// unused permissions):
//   storage      chrome.storage.session holds the extension's own auth
//                session; chrome.storage.local holds the anti-rollback
//                field-map version floor (background.ts, lib/supabase.ts).
//   sidePanel    the whole UI (entrypoints/sidepanel, setPanelBehavior).
// `activeTab` and `scripting` were in this list from the original E2
// sketch and never used: the content script is declared statically, and
// nothing calls chrome.scripting or reads a tab's URL/title.
//
// `host_permissions` is scoped to exactly the four ATS hosts the content
// script runs on, requested at install time, with no `<all_urls>`. E4/E5
// made this an explicit call rather than switching to per-ATS
// `optional_host_permissions` progressive consent: inventing a consent
// flow was new product surface, not a port of E2's precedent. Nothing in
// the current code needs them on top of `content_scripts.matches` -- they
// stay because dropping them changes the manifest's install-time behavior,
// which wants a check in a real Chrome first, not because a feature
// depends on them.
//
// `minimum_chrome_version` 148: every message listener here answers by
// RETURNING A PROMISE (background.ts, content.ts), which Chrome documents
// as supported from 148 (shipped together with the `browser` namespace --
// Chrome's "Transition to browser namespace" page; the earlier attempt in
// 144 is not a safe floor). On an older Chrome each reply silently arrives
// as `undefined` and the panel never gets any state. It also clears the
// Ed25519 WebCrypto floor (137) that signed field-map verification needs,
// and `chrome.sidePanel` (114). Below it the store shows "not compatible"
// instead of installing a dead extension.
export default defineConfig({
  modules: ["@wxt-dev/module-react"],
  // WXT's own default output dir (`.output/`) is dot-prefixed, which
  // macOS's native file picker hides by default -- "Load unpacked" in
  // chrome://extensions couldn't even see it without an extra Cmd+Shift+.
  // to reveal hidden folders. A plain `build/` avoids that friction on
  // every single reload during development.
  outDir: "build",
  hooks: {
    // `wxt zip` builds first and fires this only afterwards, so nothing has
    // been written to the zip yet when this throws. Local `wxt build` is
    // deliberately not guarded -- see scripts/store-build-guard.ts.
    "zip:start": (wxt) => assertStoreBuild(process.env, path.join(wxt.config.outDir, "manifest.json")),
  },
  manifest: {
    name: "Between Jobs",
    description:
      "Deterministic ATS autofill from your own prepared résumé and cover letter. You always click submit.",
    homepage_url: "https://between-jobs.tech",
    minimum_chrome_version: "148",
    permissions: ["storage", "sidePanel"],
    host_permissions: [
      "https://jobs.lever.co/*",
      "https://job-boards.greenhouse.io/*",
      "https://boards.greenhouse.io/*",
      "https://jobs.ashbyhq.com/*",
    ],
    action: { default_title: "Between Jobs" },
  },
});
