import { defineConfig } from "wxt";

// browser-extension.md E2 -- Lever only for now (D1: prove the engine
// deeply on one ATS before touching Greenhouse/Ashby), Chrome only (D2:
// chrome.sidePanel has no compatible equivalent in Firefox/Safari).
// `host_permissions` is scoped to exactly the one host the content script
// needs, requested at install time -- Proposal §29.2's own permission
// discipline ("do not ask for access to every website without a clear
// feature reason") and Chrome Web Store's own single-purpose scrutiny
// (browser-extension.md's Chrome MV3 research) both call for the
// narrowest permission that supports the feature, not `<all_urls>`.
export default defineConfig({
  modules: ["@wxt-dev/module-react"],
  // WXT's own default output dir (`.output/`) is dot-prefixed, which
  // macOS's native file picker hides by default -- "Load unpacked" in
  // chrome://extensions couldn't even see it without an extra Cmd+Shift+.
  // to reveal hidden folders. A plain `build/` avoids that friction on
  // every single reload during development.
  outDir: "build",
  manifest: {
    name: "Between Jobs",
    description:
      "Deterministic ATS autofill from your own prepared résumé and cover letter. You always click submit.",
    permissions: ["storage", "sidePanel", "activeTab", "scripting"],
    host_permissions: ["https://jobs.lever.co/*"],
    action: {},
  },
});
