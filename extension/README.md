# Between Jobs browser extension

Deterministic ATS autofill from your own prepared résumé and cover letter.
**The human always clicks submit -- this extension never does.**

Scoped in `~/.claude/plans/browser-extension.md` (private planning doc,
not in this repo). Supports Lever, Greenhouse, and Ashby (E2/E4/E5).
Workday and other ATSs are explicitly out of scope for this phase.

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
are plain, unremarkable HTML-form conventions, not curated ATS-specific
IP -- those ship open source and unsigned as `GENERIC_FIELD_DEFAULTS` in
`lib/lever.ts`/`lib/greenhouse.ts`/`lib/ashby.ts`, so a fresh self-hosted
clone gets baseline autofill with zero setup, before anyone has published
a signed map at all. **Greenhouse and Ashby (E4/E5) go further than that
split for now**: no real signed map has ever been curated or published
for either (that's a maintainer/secret-custody step, not done as part of
E4/E5), so their entire engine -- including custom-question extraction
and, for Greenhouse, cover-letter attach -- is open source and unsigned
today, exactly the state Lever itself was in between E2 and E3c. See each
file's own top-of-file comment for the real, live-confirmed DOM research
behind it, and the disclosed follow-up needed to close the same
CLAUDE.md compliance gap E3c already closed for Lever.

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
- `entrypoints/content.ts` -- runs on `jobs.lever.co`,
  `job-boards.greenhouse.io`/`boards.greenhouse.io`, and
  `jobs.ashbyhq.com` pages. Detects which ATS (if any) the current page
  belongs to by hostname, dispatches to that ATS's own engine
  (`lib/lever.ts`/`lib/greenhouse.ts`/`lib/ashby.ts`), fills only the
  fields it was explicitly told to fill, and never touches anything else
  on the page -- no checkbox, no button, no submit, on any of the three.
- `entrypoints/sidepanel/` -- the UI. Shows sign-in, detection status, a
  manual "Fill this page" trigger, and which fields still need your own
  answer.
- `lib/standardFields.ts` -- the shared, ATS-agnostic fill mechanics
  (field-fill planning, D5 idempotency, file attach) every ATS engine
  reuses, plus `setReactControlledValue`/`applyReactControlledFillPlan`
  (E4/E5) -- the native-setter workaround Greenhouse's and Ashby's own
  React-controlled inputs need, confirmed live neither Lever needs nor
  breaks anything by not using.
- `lib/lever.ts` / `lib/greenhouse.ts` / `lib/ashby.ts` -- each ATS's own
  fill logic (DOM traversal, event dispatch, custom-question extraction),
  kept pure and DOM-testable on purpose (`tests/lever.test.ts`,
  `tests/greenhouse.test.ts`, `tests/ashby.test.ts`). Lever's takes the
  verified field map as a parameter (E3c); Greenhouse's and Ashby's are
  fully self-contained today (see the note above).
- `lib/ats-field-map.ts` -- the Ed25519 signature verification for a
  fetched field map (`tests/ats-field-map.test.ts`, including real
  tamper/mismatch rejection tests, not just happy-path checks), generic
  across all three `ats_type`s.
