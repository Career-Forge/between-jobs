import { defineConfig } from "wxt";

// browser-extension.md E2 -- Lever only originally (D1: prove the engine
// deeply on one ATS before touching Greenhouse/Ashby), Chrome only (D2:
// chrome.sidePanel has no compatible equivalent in Firefox/Safari).
// `host_permissions` is scoped to exactly the hosts the content script
// needs, requested at install time -- Proposal §29.2's own permission
// discipline ("do not ask for access to every website without a clear
// feature reason") and Chrome Web Store's own single-purpose scrutiny
// (browser-extension.md's Chrome MV3 research) both call for the
// narrowest permission that supports the feature, not `<all_urls>`.
//
// E4/E5 -- real judgment call, made explicitly rather than silently:
// extended this same flat list with Greenhouse's and Ashby's real,
// live-confirmed domains (`job-boards.greenhouse.io` -- the modern
// surface every real posting tested redirects to; `boards.greenhouse.io`
// kept too since a URL there is still the first thing the content script
// sees before any redirect completes; `jobs.ashbyhq.com`, confirmed live
// against three real orgs), rather than switching to the
// `optional_host_permissions` progressive-request pattern this file's
// comment used to gesture at. Reasoning: browser-extension.md's own E6
// ("Hardening + store-readiness pass") explicitly names Chrome Web Store
// permission-justification requirements as ITS scope, not this one's --
// inventing a per-ATS progressive-consent UX here would be new product
// surface, not a mechanical port of E2's own precedent to two more ATSs.
// Matches this task's own stated recommendation; flagged as a real fork,
// not an obvious default, since a stricter reviewer could reasonably
// prefer progressive consent starting now instead of at E6.
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
    host_permissions: [
      "https://jobs.lever.co/*",
      "https://job-boards.greenhouse.io/*",
      "https://boards.greenhouse.io/*",
      "https://jobs.ashbyhq.com/*",
    ],
    action: {},
  },
});
