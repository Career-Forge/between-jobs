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

Requires Chrome 148 or newer to run (`minimum_chrome_version` in the
manifest): every message listener answers by returning a promise, which
Chrome documents as supported from 148. On an older Chrome each reply
would arrive as `undefined` and the panel would never get any state.

## Loading it in Chrome for real testing

`npm run build` writes an unpacked extension to `build/chrome-mv3/`.
Load it via `chrome://extensions` -> enable Developer mode -> "Load
unpacked" -> select that directory. After any code change, `npm run
build` again and click the reload icon on the extension's card.

Needs the between-jobs API running locally (`uvicorn
between_jobs.api.app:app --reload --port 8012` from the repo root, or the
`between-jobs-api` entry in `.claude/launch.json`) for anything beyond
the sign-in screen to work.

## Building for the store

`npm run build` is for local testing: it reads `.env.local`, so the unpacked
extension talks to your local API. **Never upload that.** The store zip is
built with `npm run zip`, which refuses to package anything unless
`WXT_API_BASE_URL` and `WXT_SUPABASE_URL` are real `https://` non-local
origins, `WXT_SUPABASE_PUBLISHABLE_KEY` is set, and the manifest version is
a valid non-zero Chrome version (`scripts/store-build-guard.ts`). Without
that check a leftover `.env.local` ships an extension wired to
`http://localhost:8012`, and with no env at all one that calls
`fetch("undefined/...")` -- neither fails at build time.

Vite gives `.env.local` higher precedence than `.env.production`, so pass
the production values in the shell (shell values beat every `.env` file) and
build from a clean checkout:

```
WXT_API_BASE_URL=https://... \
WXT_SUPABASE_URL=https://... \
WXT_SUPABASE_PUBLISHABLE_KEY=... \
npm run zip
```

Bump `version` in `package.json` for every upload -- the store requires each
version to exceed the last, and `0.0.0` is not a valid Chrome version.

### What each permission is for (store listing justifications)

- `storage` -- `chrome.storage.session` holds the extension's own sign-in
  session (never readable by content scripts); `chrome.storage.local` holds
  the highest signed field-map version accepted per ATS (an anti-rollback
  floor that has to survive a browser restart).
- `sidePanel` -- the entire UI lives in the side panel.
- Host access to `jobs.lever.co`, `job-boards.greenhouse.io`,
  `boards.greenhouse.io` and `jobs.ashbyhq.com` -- where the content script
  runs. No other site is ever touched.

The extension requests no `activeTab`, `scripting`, `tabs`, cookies or
`<all_urls>`. What it sends the backend: the tab's posting URL (no query
string or fragment) when it looks the page up, and -- only when you click
the matching button -- the text of a custom question (to find or draft an
answer), an answer you choose to remember, or an application id when you
mark it applied. Self-identification questions (gender, race, disability,
veteran status and the like) are never listed, drafted or filled.

### Known limitations (v1)

- EU-hosted boards (`jobs.eu.lever.co`, `job-boards.eu.greenhouse.io`) are
  not matched.
- Greenhouse application forms embedded in a customer's own careers page
  (`boards.greenhouse.io/embed/job_app?...` inside an iframe) aren't
  reached: content scripts don't run in subframes here, and an embed URL
  never matches a tracked posting anyway.
- Workday and every other ATS are out of scope.

## Chrome Web Store readiness

Nothing has been submitted and no store account exists yet. The store-facing text is
drafted, from the code as it is, in `store/` (all three marked DRAFT, none is legal
advice):

- `store/PRIVACY.md` -- the privacy policy to host at a public URL. Its last section is
  maintainer-only notes and must be deleted from the hosted copy.
- `store/PERMISSIONS.md` -- one justification per permission and host permission, and the
  remote-code answer (none: signed data only).
- `store/LISTING.md` -- name, summary, description, category, single-purpose statement,
  the Privacy practices data-usage answers row by row, draft in-product disclosure copy,
  reviewer test instructions, and the screenshot shot list.

What still needs a person:

- A Chrome Web Store developer account, and the production API and sign-in origins to
  build the zip against (see "Building for the store").
- Every `[MAINTAINER TO FILL: ...]` placeholder in `store/`, and the policy hosted at a
  public https URL.
- An in-product disclosure and consent screen. Chrome requires the disclosure inside the
  extension with an explicit agree action; the side panel has none yet. This is a code
  change (draft copy in `store/LISTING.md`, section 4).
- A real icon (the current one is a placeholder), 1 to 5 screenshots at 1280x800, and a
  440x280 promo tile.
- A reviewer test account, with tracked postings on each service and a spend-capped AI
  provider key (`store/LISTING.md`, section 5).
- Real-browser verification that no earlier phase could do: E3b (Draft answer, Fill and
  Fill & remember against a live Greenhouse or Ashby page and a real provider key) and
  E3c (a signed Lever field map on a live Lever page), plus a smoke test on Chrome 148 or
  newer of the E6 changes -- the side panel recognized by `sender.url`, a single-page
  navigation on Greenhouse or Ashby refreshing the panel, and the draft box sizing.
- A decision on `host_permissions` (redundant with the content-script matches today) and
  on the wildcard CORS the extension's API access relies on (`store/PERMISSIONS.md`,
  note 1).

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
- `lib/questionSafety.ts` -- the guards every ATS's custom-question path
  shares, at extraction and again at fill: the D6 self-identification
  classifier (gender, race, disability and accommodation, veteran status and
  so on -- never listed, drafted or filled; work-authorization questions are
  deliberately not in scope), fail-closed control classification (only a
  plain text field with a readable label is ever offered for drafting), and
  label sanitizing. Question labels are tenant-controlled text, so nothing
  here trusts their spelling.
- `lib/atsHosts.ts` -- which ATS a host belongs to, and the canonical
  posting URL a tab is looked up by (no query string or fragment).
- `lib/ats-field-map.ts` -- the Ed25519 signature verification for a
  fetched field map (`tests/ats-field-map.test.ts`, including real
  tamper/mismatch rejection tests, not just happy-path checks), generic
  across all three `ats_type`s.
