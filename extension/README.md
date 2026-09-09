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

The genuinely ATS-idiosyncratic parts of a field map (Lever's
`urls[LinkedIn]`/`urls[Other...]` field-name convention, the `cards[`
custom-question prefix, the `.application-field`/`.application-label`
DOM-nesting shape, the cover-letter label pattern) are hosted-service
data, never committed here, for the same reason this repo never commits
ATS registry data or scoring rubrics -- see the root `CLAUDE.md`. As of
E3c, this data is fetched at runtime from `GET
/extension/field-maps/{ats_type}` as an Ed25519-signed, versioned JSON
payload and verified client-side (`lib/ats-field-map.ts`) before this
extension trusts anything in it -- a failed or missing signature means
this extension refuses to use that ATS's curated data at all (no
fallback to a cached or bundled copy). The real curated content and the
matching private signing key both live entirely outside this repo;
`scripts/sign_and_publish_ats_field_map.py`'s own docstring explains how
a maintainer generates a keypair and publishes a map.

A handful of fields (`name`/`email`/`phone`, the résumé-upload selector)
are plain, unremarkable HTML-form conventions, not curated Lever-specific
IP -- those ship open source and unsigned as `GENERIC_FIELD_DEFAULTS` in
`lib/lever.ts`, so a fresh self-hosted clone gets baseline autofill with
zero setup, before anyone has published a signed map at all.

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
- `lib/lever.ts` -- the actual fill logic (DOM traversal, event
  dispatch, file attach), kept pure and DOM-testable on purpose
  (`tests/lever.test.ts`). Takes the verified field map as a parameter
  rather than hardcoding Lever's own curated data -- this file is
  ATS-agnostic mechanism, not ATS-specific knowledge.
- `lib/ats-field-map.ts` -- the Ed25519 signature verification for a
  fetched field map (`tests/ats-field-map.test.ts`, including real
  tamper/mismatch rejection tests, not just happy-path checks).
