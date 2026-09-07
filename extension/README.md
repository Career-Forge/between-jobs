# Between Jobs browser extension

Deterministic ATS autofill from your own prepared résumé and cover letter.
**The human always clicks submit -- this extension never does.**

Scoped in `~/.claude/plans/browser-extension.md` (private planning doc,
not in this repo). Lever only for now (E2) -- Greenhouse and Ashby come
later, once the core mechanism is proven on one ATS.

## What's open source vs. hosted

This extension shell -- the manifest, service worker, content script, and
side panel in this directory -- is open source, since it reads pages and
touches your personal data; you should be able to verify what it does.
The selector maps it will eventually fetch (which CSS selector on which
ATS maps to which field) are hosted-service data, not committed here, for
the same reason this repo never commits ATS registry data or scoring
rubrics -- see the root `CLAUDE.md`. E2 uses a small hardcoded map for
Lever's standard fields while that hosted-map serving design (signing,
versioning) gets built in a later phase.

## Dev setup

Requires Node 20+.

```
cd extension
npm install
cp .env.example .env.local  # fill in the same Supabase project values web/.env.local uses, plus the API base URL
```

Toolchain (all must pass before a commit touching this directory):

```
npm run typecheck
npm run build
npm run test
```

## Loading it in Chrome for real testing

`npm run build` writes an unpacked extension to `build/chrome-mv3/`.
Load it via `chrome://extensions` -> enable Developer mode -> "Load
unpacked" -> select that directory. After any code change, `npm run
build` again and click the reload icon on the extension's card.

Needs the between-jobs API running locally (`uvicorn
between_jobs.api.app:app --reload --port 8012` from the repo root, or the
`between-jobs-api` entry in `.claude/launch.json`) for anything beyond
the sign-in screen to work.

## Architecture

- `entrypoints/background.ts` -- the service worker. Owns the extension's
  own Supabase Auth session and every backend call. Never exposes
  provider/LLM keys (none exist client-side to expose).
- `entrypoints/content.ts` -- runs on `jobs.lever.co` pages. Detects a
  real Lever application form, fills only the fields it was explicitly
  told to fill, and never touches anything else on the page -- no
  checkbox, no button, no submit.
- `entrypoints/sidepanel/` -- the UI. Shows sign-in, detection status, a
  manual "Fill this page" trigger, and which fields still need your own
  answer.
- `lib/lever.ts` -- the actual field-mapping and fill logic, kept pure
  and DOM-testable on purpose (`tests/lever.test.ts`) -- this is the part
  most likely to need updating as Lever's own markup drifts.
