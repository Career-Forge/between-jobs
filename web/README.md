# Between Jobs web app

React and TypeScript on Vite, a thin client over the API. It is built to static files and can be
served from any static host; `web/.env.example` lists the three variables it reads.

```bash
npm ci
cp .env.example .env.local     # VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY
npm run dev                    # http://localhost:5173, /api is proxied to the API on :8012
npm run typecheck
npm test
VITE_API_BASE_URL=https://api.example.com VITE_SUPABASE_URL=https://<project>.supabase.co npm run build
```

A production build needs `VITE_API_BASE_URL` and `VITE_SUPABASE_URL`, both `https://` origins that
are not local addresses, and refuses to run without them (`scripts/apiBaseGuard.ts`,
`scripts/securityHeaders.ts`). Pass them in the shell rather than in `.env.local`, so a leftover
local file cannot leak into a bundle you meant to deploy. A change to either needs a new build.

A self-hosted or staging build also sets `VITE_SITE_URL` (an `https://` origin) so the link-preview image
tags name its own host; left unset they name the hosted site (`scripts/siteOrigin.ts`).

## Shell, selection and buttons

`src/components/AppShell.tsx` is the signed-in frame. From 960px wide it is a 220px left rail (wordmark,
the main navigation, then the signed-in address, Settings, Sign out and the legal links). Under 960px the rail is replaced by a slim top bar and a bottom tab bar with four tabs and a
More button, whose sheet holds the entries that have no tab. Both navigations are always in the markup and
CSS shows one; the destinations come from one list in `src/lib/nav.ts`. The page is the one `<main>`, and the
first tab stop is a "Skip to content" link to it; on a page that draws a next-step band (Today, Discover, Applications, Practice, Hiring signals, Profile and Settings,
`PATHS_WITH_A_BAND` in `AppShell.tsx`, and an application's workspace, `pageHasBand`) a "Skip to next step" link to the band's button comes first,
except while the tester-programme gate draws its own page, or its wait, at one of those addresses: the gate says so
(`components/ShellBandContext.tsx`) and the link is left out rather than pointing at nothing. The sign-in
page has both. The command palette and the assistant drawer are reserved
(`src/components/ReservedSlots.tsx`): `hidden` and inert, so they take no room, print no placeholder and have no
behavior.

One style marks a chosen item everywhere: a maroon fill with ivory text and weight 600, set by the item's
ARIA state (`aria-current`, `aria-pressed`, `aria-selected` or `aria-checked`) on an element with
`.bj-selectable`; a group of them sits in `.bj-segmented`. Focus is separate: a 2px cyan outline, offset 2px.
Buttons are primary (`.bj-primary`, the one cyan fill on a page), secondary (a plain `<button>`, outlined),
quiet (`.bj-quiet`, text only) or a cyan text link. The tokens are in `src/tokens.css`, and
`src/tokens.test.ts` computes the contrast of every pair they introduce.

Two properties of the shell are choices rather than accidents. The rail and the page share the one canvas
color (`--bj-bg`) and are told apart by the rail's 1px border: a page tinted a step lighter would sit under
every card that already uses `--bj-surface` and flatten them all. And the chosen item is a fill with no edge
bar and no underline, so under a forced-colors mode (Windows High Contrast) it is redrawn as a system
`Highlight` fill rather than left to the browser, which would erase it.

## Trail, next-step band and state primitives

Small building blocks the pages are being moved onto. None of them fetches anything: a page hands in
what it has read, and they draw only that.

- `src/lib/trail.ts` is the grammar of the trail line (`[glyph] actor · basis · when`): `buildTrail`
  turns the slots a page was given into a line, leaving out any slot that was not returned together with
  its separator, and `buildReceipt` does the same for the receipt that opens under it. It owns the glyph
  set (`TRAIL_GLYPHS`), refuses the words a trail never says ("verified" about a link, "unknown model"),
  and has date helpers that return null rather than guess. `components/paper/TrailLine.tsx` draws it: a
  44px button that opens the receipt inline, or as a focus-trapped bottom sheet
  (`components/paper/BottomSheet.tsx`) under 960px; `StaticTrailLine` is the same line with nothing to open.
- `src/lib/nextStep/` is the rule table behind the band, as a pure function:
  `nextStep(input)` answers `loading`, `none` or a step `{ sentence, action, why }`. Global rows
  (`global.ts`: signed out, setup check failed, no profile, no usable model key, the page's own read failed)
  run before a page's own rows, and the first row that applies wins. To add a page, write its
  rows in a new file `src/lib/nextStep/pages/<name>.ts` that exports `rules = definePage(...)`. The registry is
  built from the files in that folder, so there is no list to edit, and a duplicate page name or row id fails
  when the module loads.
  `components/paper/NextStepBand.tsx` draws the answer and holds the page's only cyan fill. While the page
  has a request out for the step it passes `pending={{ intent, label }}`: every handler button and the form
  submit button (named `SUBMIT_INTENT`) turn `aria-disabled` and ignore presses, and the one named by
  `intent` reads `label` ("Generating...", "Signing in..."). A route link is left alone.
  "No usable model key" is one row, G3, read by every page that supplies `credentials.modelKey`
  (`lib/modelKey.ts`: a key counts only when it is checked and has a model chosen). Its words follow the
  setup checklist: "Add a model key", or "Choose a model" for a saved key that has none. Settings does not supply
  it; its own rows (S1, S2, S2k) say the same thing with buttons that act in place.
- `components/paper/PageHeader.tsx` (breadcrumb, the page's one `<h1>`, subtitle, actions) and
  `components/paper/StateViews.tsx` (loading, empty, partial, stale, setup-required, failure). `LoadingState`
  takes `announce={false}` where the band already says the same thing in a live region. `FailureState` takes
  `restoreFocusTo` for a Retry that removes the callout it sits in; the handoff lives in its own marker
  component so that `FailureState` itself stays a plain function of its props, which the DOM-free tests in
  `src/testing/reactTree.ts` rely on (they call components as functions).
- Their styles are in `src/styles/paper.css`, which defines no color of its own.

Tests that need a DOM (events, focus) start with `// @vitest-environment jsdom` and use
`src/testing/render.tsx`. `src/testing/a11y.ts` runs axe-core over a rendered container and fails on a
serious or critical violation; jsdom has no layout, so it cannot check color contrast (`tokens.test.ts` does
that for the token pairs).

The suite runs in UTC whatever zone the machine is in (`src/testing/pinTimeZone.ts`), because the pages write
a date in the reader's own zone. `TEST_TZ=America/New_York npm test` runs it in another zone; fixtures keep
their instants mid-day UTC so that it passes from UTC-10 to UTC+11. A shell that exports `TZ` is not honoured.

`src/appPages.test.tsx` mounts the whole signed-in app with the API faked and runs every page through each
account state that changes its band (no profile, a profile with no key, a key with no model, all set and empty,
all set with data) at a phone and a wide width, plus the workspace, Discover and Hiring signals states that move
the band. For each it checks one band, one main heading, at most one cyan fill, no duplicate ids and no link to a
place on the same page that does not exist, also while a page's setup reads are still out and its band is
loading. Every signed-in page also has a title of its own, and no content sits outside a landmark. It also checks that the places other pages link to (Settings
sections, the profile's import, each workspace section) are there. A page that adds a second filled button or a
second band fails here, whichever page it is.

## Document pages

The pieces a long, document-like page (the application workspace, Profile) is built from. They hold no data:
a page hands in what it has read and they draw only that. Their styles are in `src/styles/paper-doc.css`
(tokens only, no color of its own, no cyan fill). The page's one cyan fill is still the band's button.

**`DocumentLayout`, `DocumentBlock`, `BlockTrail`, `TrailMargin`** (`components/paper/DocumentLayout.tsx`).
A column of blocks, each with its trail line, where the trail goes by width and the page does not know which:

- 1280px and up: a 280px right margin with a 1px maroon rule. A block's trail line is a button in the margin
  beside it; opening it shows the receipt in the margin (`aria-expanded` on the button).
- 960 to 1279px: no margin. The trail line sits under its block and the receipt opens inline, as a
  disclosure. The page's `summary` (a compact "current state" it writes itself) is drawn once at the top.
- Under 960px: the trail line sits under its block and the receipt is the focus-trapped bottom sheet.

```tsx
<DocumentLayout summary={<p>Status saved · resume drafted</p>} measure="wide">
  <SectionChapter id="ch-draft" title="Draft" />
  <DocumentBlock trail={{ glyph: "code", actor: "Checked by code" }} receipt={rows} receiptTitle="Where this came from">
    ...
  </DocumentBlock>
  <DocumentBlock id="generate">
    <GeneratePanel />   {/* calls <BlockTrail trail={...} receipt={...} /> itself, wherever it likes */}
  </DocumentBlock>
</DocumentLayout>
```

`DocumentLayout` props: `children`, `summary?`, `summaryLabel?` ("Current state"), `measure?` ("wide" 880px
by default, "prose" 680px, "full"), `className?`. `DocumentBlock`: `children`, `id?`, `trail?` / `receipt?` /
`receiptTitle?` (shorthand for a `BlockTrail` as its last child), `className?`. `BlockTrail`: `trail`,
`receipt?`, `receiptTitle?`, `className?`, the same inputs as `TrailLine`. Rules that hold at every width:
the DOM order is block, then its trail line, then the next block (the margin never forms a tab run of its
own); only one receipt is open in the whole layout; Esc closes an inline or margin receipt and puts focus back
on its line; a receipt is closed when the width crosses a breakpoint, in the same render that notices the
crossing, so it is never drawn in its new place (not even as a sheet that takes focus) for a moment.
Blocks may sit inside a wrapper such as a `<section>` that groups a chapter with its blocks: the margin cells
and the rule still line up, and a `SectionChapter` is kept in the document column wherever it is. Other loose
content at 1280px and up should be a direct child of the layout (it is kept in the column) or inside a block;
inside a wrapper it would run under the rule. `useDocumentMode()` answers "margin", "inline" or "sheet" for the current width. The layout reads
the viewport in script and puts the answer on the markup as a class, so the stylesheet states no width of its
own for it. `BlockTrail` outside a layout is a plain `TrailLine`.

**`JumpBar`** (`components/paper/JumpBar.tsx`). The section index: groups of real `<a href="#id">` links
(`groups={[{ label, links: [{ id, label }] }]}`, a group with no links is left out; pass `ready` once the
sections are on the page). At 1280px and up one sticky row; 960 to 1279px the groups wrap whole and, once the bar
has scrolled away, a 44px "Jump to" row docks at the top; under 960px only the "Jump to" select, in a sticky row,
with the same groups and labels. A pick with the pointer or from the open list goes at once; on a closed select
the keyboard's Arrow, Home, End and type-ahead steps wait for Enter (Tab or Esc drops them), because browsers
fire a change per step. Until a section has been reached the select reads "Choose a section". The section being read is marked `aria-current="location"` by a scroll watch
(`lib/useScrollSpy.ts`) that only marks: it never moves focus and never scrolls. Following a link changes the
hash and `useScrollToHash` scrolls (instantly under reduced motion) and focuses the section's heading; the bar
calls that hook, so the page does not. Hash targets stay clear of the bar by scroll padding on the page.

**`SectionChapter`** (`components/paper/SectionChapter.tsx`). The heading block of a section: `id?`, `title`,
`level?` (2 to 6, h2 by default), `eyebrow?`, `trail?`, `children?` (an intro). The heading is serif, takes
`tabIndex={-1}` so a hash link can focus it, and has no numeral.

**`SegmentedBar`** (`components/paper/SegmentedBar.tsx`, arithmetic in `lib/segments.ts`). One score part as a
row of cells with its label and value in words: `label`, `value` (a number, or `null` for nothing to draw),
`max`, `segments?` (one per point up to 35, else 20), `valueText?` ("27 / 35 · near max"; take the "near max" from
`barState()` in `lib/scoreBreakdown.ts`, never by hand), `hatched?` (exact headroom that is not earned: hatched
gold cells after the filled ones), `detail?` (a caption). A value between 0 and the maximum never draws an empty
bar or a full one; with one cell per point the filled count is the value. It is a `meter` named by its label
with the words as its value text, or an `img` when there is no value, so it is never read as zero. Never a gauge,
ring or radar.

**`ScoreBlock`, `FitLadder`** (`components/paper/ScoreBlock.tsx`). `ScoreBlock` is the ATS block's frame: `label`,
`value`, `max?` (100), `from?` (draws "64 -> 72" only when it differs, and says "was 64, now 72" to a screen reader), `caption?` ("medium confidence"),
`engine?` (the "engine ✦" mark, on by default), `children` (the bars), `footer?`. `FitLadder` is Discover's
four-rung ladder: `levels` (lowest first), `level` (the rung's name, from the engine; there is no score-to-rung
mapping here), `score?`, `max?`, `placeholder?` (hollow, no number, for a fallback). The "engine" caption is drawn only when a rung
was reached, never for "Not scored" or a placeholder. Use them only for a score
that code computed; a model's judgement gets the model mark in its trail, not this.

**`ClaimRow`, `ClaimList`** (`components/paper/ClaimRow.tsx`). One claim and what is known about it: `status`
("supported", "unverifiable", "contradicted" or "unchecked"), `claim` (verbatim, without quotation marks), `reason?`,
`actions?`, `as?` ("li" or "div"). Each status has a word and a mark as well as an outline: ◆ Supported (solid gold),
◆ Unverifiable (dashed gold), ✕ Contradicted (red), ○ Not checked (muted). The claim check lists only what it flagged,
so a claim it did not flag is not drawn as "supported": not flagged is not backed. Use "supported" only where
something named does back the claim. The sentences around the list are the page's to write, because the
components do not supply them: the claim check's always-visible legend ("claims that weren't flagged aren't
listed"), its "Flags only -- nothing was auto-edited or blocked" footer, and the zero-flag state with its "Not
flagged isn't verified" caveat; for the checklist, the "Checked by code · no model involved" trail and the
one-page caveat. Leaving one out turns an honest list into a verdict.

**`ChecklistGroup`, `ChecklistRow`, `ChecklistSummary`** (`components/paper/ChecklistRow.tsx`, counts in
`lib/checklistSummary.ts`). The rows of a review checklist: `ChecklistGroup` (`title`, `rows` with an `id` each,
`level?`) counts its rows in its heading; `ChecklistRow` (`state` "pass" / "fail" / "not_checked", `label`,
`hint?`, `caveat?`, `actions?`) draws ✓ Pass, ✕ Fail or ○ Not checked; `ChecklistSummary` says "5 pass · 1 fail ·
4 not checked". A row is a statement, not a control: there is no tick box, because nothing stores a "reviewed"
state and "not checked" is never a pass. Only what is in `actions` takes the keyboard.

## Settings

`src/pages/Settings.tsx` (the old Integrations page: `/profile/integrations` redirects to `/settings`, keeping its
query and fragment, because the API still names the old address in its setup errors). Its sections are components
under `src/components/settings/`: the AI provider (connected keys and their state, the model list, the default
model, adding a provider), the search and data keys, and the connections (Telegram, Discord, Gmail).

- A model key is saved first and given a model second: the models a key can use come from the key. A key with no
  model is "needs a model" (the API's `setup_state`), never a silent default, and the model list opens for it.
- The providers to add are drawn from `GET /credentials/llm/providers`; nothing about a provider is typed into
  the page. `src/lib/providerStatus.ts` says which have been seen working against the real service (none of the
  model providers other than OpenRouter has) and what each costs the person.
- "Connect with OpenRouter" is OAuth with PKCE: `src/lib/settings/openrouterOAuth.ts` makes the verifier and its
  S256 challenge, keeps the verifier and a state in `sessionStorage` for ten minutes, and on return takes the
  parameters off the address (`captureOAuthReturn`, imported first by `main.tsx`) before anything reads them. The
  code and verifier go to `POST /credentials/openrouter/exchange` and are never put in a URL, `localStorage` or
  a log. OpenRouter's callback must be this site's `/settings`; on a plain-http address that is not localhost the
  browser has no Web Crypto and the page falls back to pasting a key.
- The page's one cyan button is the next-step band's. With no model key it is "Connect with OpenRouter" and the
  same button inside the section is outlined; the rows are in `src/lib/nextStep/pages/settings.ts`.
- Provider-supplied model ids and names are untrusted text: parsed for shape in `src/lib/settings/llmProviders.ts`
  and drawn as text only.

## Today

`src/pages/Today.tsx` is the page a signed-in person lands on, and it says one thing to do next. The
next-step band holds the most urgent feed item itself -- its words, its trail line and a quiet Dismiss --
and that item is left out of the list below it, so no action shows twice. Which item that is comes from
the rows in `src/lib/nextStep/pages/today.ts` (a still-pending status proposal with a stage to move to,
then a resume the fit check held back and nothing has replaced, a resume ready for review, a high fit job;
with none of those, an unlit "nothing needs a decision"), which read only the facts the page has: a number or a name appears in a sentence only
when a returned field carries it. `src/lib/todayFeed.ts` builds those facts and each row's words, trail and
receipt from the same fields.

- Requests: `GET /today` is the page's own data (when it fails the band says so and offers Try again).
  `GET /profile/current`, `/credentials`, `/saved-searches` and `/applications` are read once (`readAccount`
  in `lib/firstRun.ts`) and shared by the band's setup rows, the setup checklist, the "In the background" trail
  and the job names in sentences. Every visit makes all five, including for an account that has finished
  setup: the band, the trail and the sentences need the lists whether or not the checklist does, so there is
  no shortcut for a finished account. Held-back resumes read `GET /applications/{id}/prepare-result` for
  the fit check's score and to learn whether a resume exists by now (a card whose application has one is
  not lit and says so); that is at most five reads (`FIT_READ_CAP`, the newest cards first) for each load of
  the feed. Gmail is named in the trail only once `GET /capabilities` (asked once by the shell, not by Today)
  says it is open to the person. Dismiss, accept, track and "Generate anyway" (the workspace's generation
  with the fit gate bypassed) keep their failures under the item they were for; while one runs, the band's
  button shows it (the pressed one reads "Moving...", "Tracking..." or "Generating..." and every handler
  button is `aria-disabled`), a polite status says what was done, and focus goes back to the band or the
  list's heading when the control that had it is gone.
- The setup checklist (`components/FirstRunChecklistView.tsx`, rules in `lib/firstRun.ts`) is five steps in
  three states -- to do, done with the real fact, can't tell or couldn't check -- and folds to one line
  ("Setup ready", or the counts) that opens again. It holds no cyan fill.
- From 1280px a right margin carries "In the background" (saved-search checks and Gmail reads, on whose key);
  below that the same lines are one disclosure line under the band. `src/styles/today.css` holds the layout
  and lifts the shell's 960px cap on the column where the margin needs the room.
- Links to set something up use `SETTINGS_PATH` and `MODEL_PROVIDER_PATH` (`lib/settingsPaths.ts`), the one place that names the Settings page.

## Applications and Practice

`src/pages/Applications.tsx` is the reading and the requests; `src/components/applications/ApplicationsView.tsx`
draws every state from props (so each can be shown and tested on its own); the band's rows are in
`src/lib/nextStep/pages/applications.ts`. Each tracked job is one card: its title and company, a status chip
(`◇ Saved`, `● Applied`, `◔ Screening`, `◑ Interviewing`, `✓ Offer`, `⊖ Rejected`, `⊘ Withdrawn`: a neutral outline with a
shape, emerald only on Offer), "✦ Resume drafted" when `resume_exists`, the applied day when `date_applied` came
back, its controls, and its trail line as the last line. The trail is built from `source_channel` and
`created_at` only (`cardTrail` in `lib/applicationsView.ts`): a channel the page does not know, or a date that
cannot be read, is left out rather than guessed.

- Requests: `GET /applications` is the page's own data (when it fails the band says so and offers Try again; a
  failed re-read after a move keeps the cards it has under a callout that says they may be out of date).
  `GET /profile/current` and `GET /credentials` are read once (`lib/applicationsSetup.ts`) because the band's
  global rows need them. A move is `POST /applications/{id}/stage` followed by a re-read of the whole list;
  tracking a job is `POST /applications` or `POST /applications/from-url` followed by the same re-read.
- The status select is always on a list card and shows the real status, so a wrong one is corrected in one
  choice. The board's cards keep "Move to..." (a required keyboard and touch fallback for drag and drop). A
  card whose move is out is `aria-disabled` and reads "Moving..."; one whose move failed stays on its old
  status with the reason under it and a Retry. The tracking forms are drawn secondary: the band's button is
  the page's one cyan fill (with nothing tracked it is "Start tracking", which scrolls to the paste form).
- The board is seven columns in one row that scrolls inside itself (`role="region"`, keyboard reachable), so the
  page never scrolls sideways. Under 960px there is no board and no List/Board toggle: a "Showing status" select
  with the counts and the cards of the chosen status, remembered per browser in `localStorage` (read and written
  inside `try`, never required).
- `#start-tracking` is the paste form (`START_TRACKING_ID`); every other hash the workspace answers to is
  `CROSS_NAV_HASH` in `lib/applicationsBoard.ts`, and `applicationsBoard.test.ts` checks that each is given to
  an element some page or component renders.
- `src/pages/Practice.tsx` is a pointer: practice sessions live in each application's workspace. It reads the
  same two setup facts for the band's global rows and nothing of its own.
- Styles are `src/styles/applications.css` (tokens only; it lifts the shell's 960px cap from 1280px for the board).

## Discover and Hiring signals

`src/pages/Discover.tsx` is the job search; `src/pages/HiringSignals.tsx` is the search for recent public posts that
mention a role. Both are list-like pages: a header, the next-step band, a filter form, and rows that each end in
their trail line, inline at every width. Neither makes a search when it opens.

- **Discover requests.** On open: `GET /profile/current`, `GET /credentials` (`lib/setupReads.ts`, so the band knows
  what is missing before any request that would need it is made) and `GET /saved-searches`. `GET /discover` runs
  only from the form (Enter, its Search, or the band's Search), one at a time, and never while the profile or model
  key is known to be missing. `POST /discover/track`, `POST /saved-searches` and the saved-search PATCH and DELETE
  are the ones the page has always made.
- **The band** (`lib/nextStep/pages/discover.ts`, rows D1 to D5; `hiring-signals.ts`, rows HS0 to HS3) holds the
  page's one cyan fill: Search when ready, Search again when the scoring fell back, Clear filters when nothing
  matched, Save this search when Hiring signals results are on the page for a search that is not saved. The form's
  own Search, every row's Track and Save are outlined. The elapsed counter of a running search is drawn beside the
  band (`role="timer"`, live region off), not in its sentence, so the sentence is announced once.
- **A Discover row** (`lib/discoverRow.ts`, drawn by `components/discover/`) is built from returned fields only. Its
  fit block is the four-cell ladder with "Good -- 61 / 100" and the "engine" mark, a bar out of 100 for each applied
  part, what was not applied and why, the lowest part, and the model's three-way reading of the location. Rows
  past the scoring batch say "Not scored"; when the scoring fell back every row says "Placeholder" with no rung
  and no number. The trail says `via registry` on the company's job board at the last poll for a registry row the
  database calls fresh, and only "not confirmed on the company's board recently" for one that is not;
  USAJobs' sponsorship line is worded as the provider's category fact ("by provider, no text read"), never a
  text match. A live-search row says "Link checked" for a live-search row the link check kept (it means not found closed, never "verified");
  a row that never reached the check says so. Only an http(s) address becomes a link.
- **Saved searches** (`components/SavedSearchesCardView.tsx`, shared with Settings through `lib/useSavedSearches.ts`)
  read "Checked 06:00" only when `last_matched_at` is later than `created_at`, otherwise "Not checked yet", and
  "Paused" after either.
- **Hiring signals** reads `GET /hiring-signals/status` once for the app, then (only when it is on) the two saved
  lists. The page also reads `GET /credentials` for the band (no profile: it needs none, so `profileNeeded: false` keeps the global profile rows off it). The model lives in the page
  (the band reads it) and the tab is mounted only when the feature is on. A failed status read, and a search that
  failed in a way trying again can change, are the band's F1; the results block carries no Try again of its own.
  Result and saved-post cards take an optional `trail` prop (`components/HiringPostCard.tsx`).
- A band button that starts a request is replaced while it runs; `lib/useBandFocus.ts` keeps keyboard focus on the
  band meanwhile.

## Profile

`src/pages/Profile.tsx` is the person's structured profile as a document: who it is, their personal
details, the resume layout defaults, the sections (each one editable), importing a new version and deleting
the account. It is drawn on `DocumentLayout` (so each block's trail line sits in the right margin from
1280px, under the block below that, and opens as a sheet on a phone), and the blocks live in
`src/components/profile/` (`ActiveProfile`, `JsonImport`, `JsonReview`) beside the older cards
(`SectionedProfile`, `PersonalDetailsCard`, `HeaderComposer`, `SectionOrderEditor`, `ShapeSettingsPanel`,
`EntryEditModal`, `ResumeImportCard`, `AccountCard`). Its styles are `src/styles/profile.css` (the page's
blocks and the import review) and `src/styles/profile-studio.css` (the layout controls and the entry
dialog, which the application workspace embeds too, so each component imports the sheet itself).

- Requests: `GET /profile/current` is the page's own data (a 404 is "no profile yet"; anything else is a
  failure the band says, with Try again). `GET /credentials` is read once, only for the band's "no usable
  model key" row. Edits and the JSON import go through `POST /profile/versions` and
  `POST /profile/versions/{id}/activate`, as they always have: every save is a new immutable version.
  A re-read after an activation, a cancel or an edit never returns the page to Loading.
- A waiting import is known only in the session that made it: no call lists a draft, so after a reload the
  band says the profile is active. The JSON import's preview replaces the page's body; a resume file's draft
  is reviewed in its own card, and the two never run at once.
- The band's rows are `src/lib/nextStep/pages/profile.ts`: an import is waiting (PR1, a link to
  `#import-review`), the person is on the review (IR1, "Confirm and make active"), or nothing is waiting
  (PR0, unlit). `profileOutcome` points the global no-profile rows at `#set-up` and puts the review's own
  step in place of G2b while the person is on the review. The band holds the page's one cyan fill: the
  buttons that do the same thing in the body (`Use this profile`, `Looks good -- activate`, a dialog's
  Save) are outlined.
- A resume file's draft is checked field by field: each value read from the file has a checkbox, and both
  `Use this profile` and the band's button stay locked until every one is checked. There is no "check all".
  The checks live in the card and are not saved. The card lets the page's band use the draft through
  `confirmAndUse` on its ref, which goes through the same guarded path as the confirm button.
- The trail line under each block is built from returned fields only (`src/lib/profileTrails.ts`): how the
  version was made (`source_kind`), when it was saved and how many versions there are, and nothing for a
  field the API did not return. No model is ever named. `HeaderComposer`, `SectionOrderEditor` and
  `ShapeSettingsPanel` draw their own trail line when given `showTrail` (off by default, so the workspace
  embeds them unchanged); `HeaderComposer` also takes `activeVersionId` to say when its preview was built
  from an earlier version of the profile.
- `EntryEditModal` is a real dialog (`lib/useModalDialog.ts`): drawn into `<body>` with the page inert
  behind it, focus kept inside, Esc to cancel (not while a save is out), and focus returned to the control
  that opened it. A failed removal, skill or achievement save is shown above the sections, and the shared
  editor's `error` is never drawn outside the dialog: a Personal details failure belongs to that card.
- Header and section rows reorder with Move up / Move down buttons as well as by dragging, and the short
  resume settings are radio groups drawn as outlined segments (`components/RadioSegments.tsx`).

## Application workspace

`src/pages/ApplicationWorkspace.tsx` is the page a person works in for one role (`/applications/<id>`). It is one
document built on the document pages' pieces (see "Document pages"): the next-step band, the section jump bar, and a
column of blocks whose trail lines sit in the right margin from 1280px, under their block from 960px and in a sheet on
a phone. The page is the reading: its requests, the band's facts and which frame it is in. The sections are
components under `src/components/workspace/` and the older panels.

**The sections, in page order, and their anchors** (one list: `src/lib/workspaceSections.ts`, whose ids are
`CROSS_NAV_HASH`'s in `lib/applicationsBoard.ts`, which the Applications menu and Today link to):

| Group | Section | Anchor | Where it is built |
| --- | --- | --- | --- |
| Resume | Tailor | `#ch-sources` | `workspace/ResumeSections.tsx`: the posting (`PostingBlock`) and `TailorPanel` |
| Resume | Draft | `#ch-draft` | `ResumeSections.tsx`: the Studio's header, structure and settings controls, the same components the Profile page draws, each under its own heading and with no card frame |
| Resume | Generate | `#generate` | `ResumeSections.tsx` + `GeneratePanel.tsx` (the honest read is `HonestFloor.tsx`) |
| Resume | Review | `#ch-check` | `workspace/ReviewSection.tsx`: ATS score, gap interview, claim check, export checks, the "You submit" block |
| Research | Company intel, Contacts, Events, Hiring posts | `#company-intel`, `#contacts`, `#warm-path-events`, `#hiring-posts` | `workspace/PanelSections.tsx` |
| Prepare | Practice, Your play | `#interview-practice`, `#your-play` | `PanelSections.tsx` |

The group labels are not anchors. Hiring posts is in the bar, and the page, only while `GET /hiring-signals/status`
says on; a link to `#hiring-posts` while it is known off lands on a note saying so. A section is
`<SectionChapter id title /> + <DocumentBlock>`: the chapter's h2 is what a link to the anchor focuses, and the block
is what a panel draws its trail in (`<BlockTrail>` as the last thing in it). A panel in `PanelSections.tsx` draws no
h2 of its own (the chapter is the heading), only outlined buttons (the band holds the page's one cyan fill), and a
failure of one panel never changes the band. Its states and wording are under "Research and prepare sections" below.

- **Requests.** `GET /applications/{id}` and `GET /profile/current` are the page's own data (a 404 on the profile is
  tolerated: the Studio, Tailor and Generate then say the profile is missing, with a link). `GET /credentials` is read
  once only to know whether a usable model key is saved (`modelKeyOf`), so Tailor, whose coverage match is a model call on
  every mount, and Generate say so and fire nothing while it is known missing. The resume part's requests are the
  generation hook's, `lib/useGeneration.ts`: the stored result read once on mount, a run, the two PDF downloads and the
  export checks (on request only: they recompile the LaTeX). Changing the status is `POST /applications/{id}/stage`, then a
  re-read.
- **The band** (`lib/nextStep/pages/workspace.ts`, one table test per row): no resume yet, generating, failed in this
  visit, held back by the fit check, flagged claims, no flagged claims, a PDF downloaded this visit, then a row per
  status from Applied on. The rows for a resume (A1c to A4) apply while the status is Saved. A why line names only
  what the page read: it never names a model or a generation time, never says a claim check "ran", and never says
  "verified". The body copy of the band's action (Generate, Download PDF, Generate anyway) is outlined.
  `lib/workspaceFacts.ts` turns the generation hook's state and the account reads into the rows' facts.
- **Frames.** Loading (breadcrumb, placeholder, the band's eyebrow only), not found (`workspaceNotFound`: a way back,
  no Try again), failed (the band names the call and offers Try again; the body's callout carries a Retry of its own
  only when a higher band row, such as setup failing or no usable key, is showing instead) and ready. A profile read that
  fails is the setup check failing (the band's G1), naming the call. The band's Try again for setup re-reads only the
  read that failed and leaves the ready page mounted, so a run, a download and Tailor's request are not lost or sent
  twice; Tailor and the Generate button wait for the credentials read to answer (a failed read counts as an answer that
  claims nothing).
- **Trails** (`lib/workspaceTrails.ts`): the page's own ("You tracked this", how, when), the generation's (with the run,
  PDF hash, ATS attempts and gate in its receipt), the ATS score (engine), the claim check, the export checks (code, no
  model) and Tailor. A slot that was not returned is left out. A result read back from storage says it was restored,
  with no date.
- **Claims** (`lib/claimWarnings.ts`) are parsed from the engines' warnings into rows: unverifiable or contradicted, the
  claim in its own words, and what the built-in engine did to it when it acted. A warning that does not read as a claim
  is shown as plain text. Zero flagged says "No claims flagged in this run" and that not flagged is not verified.
- **The export checks' "I checked this" marks** are the person's own for the visit: not saved, not counted in the
  summary, read by nothing, and cleared when a new generation resets the checks. A page that could not show a stored
  score or claim check (a failed read, or a failed run that dropped the one on screen) says "couldn't be read", which
  is not the same sentence as "none is stored".
- **Keyboard.** The Status select holds a keyboard step until Enter (a change records a stage change); a pointer choice
  applies at once. A control that is replaced by its own result hands focus on: the export button is one element for
  every state, "Park this application" moves focus to the Honest read heading, "Generate anyway" to the Generate button,
  "Close the gaps" to its block, and "I submitted this" to the band. A deep link's section is held in view while the page
  above it grows (`lib/useHoldHashTarget.ts`) until the person scrolls, presses a key or touches the page.
- **Decisions and known gaps.** The band has one practice row (A6, "Go to Practice") rather than "Start practice" and
  "Continue practice: question n of m", because the practice sessions are read by the Practice section and not by this
  page; the band claims nothing about them. Reading the sessions here to light the three practice rows (and an unlit row
  when that read fails) is not built. The three Studio controls under Draft (header, structure, settings) are the
  Profile page's components, so one restyle serves both places.
- Not built: an in-browser PDF preview (it would reuse the blob a download fetches, once per generation result) and the
  band's "Answer the gap questions" (the gap interview renders only beside a drafted result).

### Research and prepare sections

Company intel, Contacts, Events, Practice and Your play (and the Hiring posts panel inside its own section) share
one build, in `components/research/` (the evidence pattern, the empty block, the run button and its problems),
`lib/research.ts` (provider words, confidence readings, the safe-link rule), `lib/researchTrails.ts` (every trail and
receipt) and `lib/useResearchRun.ts` (the stored-run read and the run, for the three that work that way). The styles
are `styles/research.css`.

- **A run** (Company intel, Contacts, Events) searches with the person's own You.com or Firecrawl key and calls their
  model, so each section's intro says it spends their keys. Its trail is read only from the run: the model glyph and
  what it wrote, the providers that searched (`providers_used`), the day (`created_at`) and, as its one other glyph, how
  many searches failed or were skipped (one warning is one search). A field that did not come back leaves its slot out.
  A failed run keeps what the section already showed and puts a Retry beside it; a missing key or profile is a notice
  with the link that fixes it (through `lib/setupRequired.ts`) and no Retry; a stored run that could not be read is a
  Retry that reads again, and its trail is the red cross. A run that came back with nothing in it is not drawn as
  something a model wrote: its line is the plain-code circle and "Searched with ...", since the backend makes no model
  call when every search failed. GitHub's public member list needs no key, so a line that names it does not say "on
  your keys".
- **Focus.** A Retry removes the callout it is pressed in, which would drop keyboard focus on the page body, so
  `FailureState` takes `restoreFocusTo`: a control that stays (the section's run button, a person's name in Contacts,
  Practice's Submit or Start button) and is focused only if focus was inside the callout. Busy buttons are
  `aria-disabled`, and a press on one is ignored before anything else happens (a paid lookup's confirm included).
  Each section's status region exists before its words change, and Contacts has a hidden polite region for what a
  lookup, a draft or a Copy came to.
- **Evidence** is one pattern: a source link (an http or https address only, opened in a new tab and named as such) and,
  where a model gave one, its confidence as gold cells with the word. Company intel's high, medium and low are 3, 2 and 1
  cells of 3. A contact source's server value `verified` means the model read the source as clearly naming the person
  in that role, which code backed only by checking the name appears in the source's title or snippet, so it is drawn as
  "Clear match", never as a verification. Your play's citations are the same line with a gold diamond and no link. Nothing
  here is green.
- **Contacts** gives no email address or phone number with a run. Each lookup is one person at a time, behind a confirm that
  names the credit it spends, and a failure of one person's lookup or draft sits on that person's row. The server
  declining to draft or to build a brief for want of evidence (`INSUFFICIENT_EVIDENCE`) is a gold note with its own reason,
  not a failure. **An outreach draft stays a draft**: it is labeled "Draft -- not sent", every message has a Copy, the copy
  under it says nothing is sent from here, and Gmail drafting (optional, invite-only, saved as a draft in the person's own
  Gmail) is whatever `GmailDraftActions` says for the person's own Gmail state.
- **Practice** reads each answered question's stored score and feedback, so a session picked up on a later visit shows the
  answers already given, each with its own trail and the time the server stored it. An answer scored in this visit has no
  time: none came back with it. STAR is four gold segments with their words and the caption "Judged by your model". Grounding
  in company research says it is shared across users.
- **Known gaps.** A stored outreach draft is not read back after a reload (the endpoint exists and is unused), so the button
  reads "Draft outreach" again. The Hiring posts panel (`HiringSignalsPanelView.tsx`) keeps its own cards and counts
  wording; the section adds only its one trail line (what searched and what the person saved, together) and drops its own heading.

## Signed-out pages

`/` (signed out), `/login`, `/privacy` and `/terms` are the pages a stranger sees first, and each has its
own stylesheet next to the component that uses it: `styles/public.css` (the header, the one `<main>` and the
footer in `components/PublicLayout.tsx`), `styles/landing.css` and `styles/circuit.css` (the landing page),
`styles/auth.css` (sign in, register and password reset) and `styles/legal.css` (the two documents). They use
tokens only, apart from the printed resume page drawn on the landing page, which is white because it stands for
paper.

- The landing page's words are `content/landing.ts` and the five stages of its circuit are
  `content/landingCircuit.ts`, so a test can scan every claim. The circuit
  (`components/landing/CircuitDemo.tsx`) is built from a sample role that is labeled as fictional; its depicted
  buttons are outlined pictures, never controls. The hero holds the page's one next-step band, so the one cyan
  fill on the page is its button.
- A header link such as `/#circuit` scrolls and moves focus to the section's heading through
  `lib/useScrollToHash.ts`, the same hook Settings and the workspace use (the router does neither).
- The sign-in screen's cyan button is the band's: it sits outside the form and names it
  (`AUTH_FORM_ID`), so Enter in a field submits as before. The password reset form has no band and keeps its own
  button.
- The legal pages have no band, and the text itself (`content/legal.ts`) is not styled into anything else.

`pages/Landing.test.tsx` holds the guard on what the landing page may claim: a list of words it never uses, no
digits, and the disclosures that must stay (the Gmail paragraph and its Privacy Policy link, the extension's
early-tester status).

## Accessibility and responsive checks

The checks come in three layers, from the cheapest to the most real. Each finds things the one before cannot.

1. **jsdom, in `npm test`.** `src/testing/a11y.ts` runs axe-core over a rendered container and fails on a serious or
   critical violation; `src/appPages.test.tsx` mounts the whole signed-in app with the API faked and checks every page in
   every account state at 375px and 1400px; the `*Styles.test.ts` files read each stylesheet as text and pin the rules
   that keep user text inside its column and every phone control at 44px. jsdom has no layout and no focus ring, so it
   cannot see colour contrast, overflow, or a control hidden behind the phone's bars.
2. **A real browser, against the real components.** Serve the app from a scratch folder outside the repository and stub
   its three seams, so any page can be shown in any state without a backend:

   ```js
   // vite.config.mjs, in a folder of your own, with node_modules linked to web/node_modules
   const WEB = "/path/to/between-jobs/web";
   const swapped = {
     [`${WEB}/src/lib/api.ts`]: path.resolve("mock/api.ts"),        // apiFetch, ApiError, apiFetchBlob, apiFetchBytes
     [`${WEB}/src/auth.tsx`]: path.resolve("mock/auth.tsx"),        // AuthProvider, useAuth
     [`${WEB}/src/lib/supabase.ts`]: path.resolve("mock/supabase.ts"),
   };
   function swapModules() {
     return {
       name: "swap-modules",
       enforce: "pre",
       async resolveId(source, importer, options) {
         if (!importer || importer.includes("/mock/") || !source.startsWith(".")) return null;
         const resolved = await this.resolve(source, importer, { ...options, skipSelf: true });
         return resolved && swapped[resolved.id] ? swapped[resolved.id] : null;
       },
     };
   }
   ```

   `src/testing/fakeApi.ts` is the stand-in the tests use and is a good base for `mock/api.ts`: describe each route, make an
   unknown one answer 404, and let a request stay pending to draw a loading state. Mount `src/App.tsx` inside the
   providers `src/main.tsx` uses. Then, for each page and each state the page's own tests list (loading, partial, empty,
   stale, setup-required, failure, and one with plenty of data), at 320, 375, 768 and 1280px:

   - **Contrast and structure.** Load `node_modules/axe-core/axe.min.js` into the page, scroll to the top (a sticky bar
     over a control makes axe report a target-size problem that is only the scroll position), and run
     `axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] } })`.
     Fix every serious and critical violation. Read the `incomplete` list too: axe cannot decide contrast over a
     pseudo-element or an overlap, so those few nodes are checked by hand.
   - **Overflow.** `document.documentElement.scrollWidth > document.documentElement.clientWidth` is the page scrolling
     sideways. A box can also be narrow enough while its text runs out of it, so find the innermost element whose
     `scrollWidth` is larger than its `clientWidth` and whose ancestors do not scroll. Do the run again with long
     unbroken strings (an address, a URL, a 90-character company name) in every field a person types.
3. **The keyboard, with real key events.** Drive the page with the browser's key events (for example Chrome's remote
   debugging `Input.dispatchKeyEvent`), not `element.click()`. For each page press Tab from the top and check that every
   stop shows a focus ring (`:focus-visible`), is not under the sticky top bar or the tab bar, is not off screen, and
   comes in an order a person would expect. Then press Enter or Space on each control and look at `document.activeElement`
   a moment later: a control that was replaced, removed or turned off by what it did must hand focus on (to the next row,
   the band, the field it opened), never leave it on `<body>`. Open and close each dialog and sheet with the keyboard.

Two conventions come out of the third layer. A control that starts a request is `aria-disabled` while it is out, never
`disabled`, and the handler ignores a press: a disabled control drops keyboard focus to the top of the page. And a
control that goes away when it is pressed (Delete, Dismiss, Add, Save) names where focus goes next, as in
`lib/useFocusAfterRender.ts`, `useSavedSearches.ts` or `SectionedProfile.tsx`.

Pages are loaded the first time they are opened: `src/App.tsx` imports the signed-in pages with `lazy`, and
`components/RouteBoundary.tsx` draws the band's loading frame while a file is on the way, and a plain failure with a
Retry if it cannot be fetched (a tab opened before a deploy asks for a file the new build no longer has). The landing,
sign-in and legal pages and the shell are in the first download, so a visitor who only reads them loads about a third
less script than the whole app. A test that mounts `App` opens a page, so it loads the pages up front
(`beforeAll` in `src/appPages.test.tsx`).

## Fonts and brand files

The typefaces are Newsreader (display), IBM Plex Sans (body) and IBM Plex Mono (provenance), served from
the app's own origin and never from a font CDN. `src/fonts.css` is the only place that names the files,
which live in `src/fonts/` with their license texts; `THIRD_PARTY_FONTS.md` says where each came from and
records a SHA-256 for every file, which `npm test` checks.

The favicon, touch icons, web app manifest and the link-preview image are in `public/` and are linked from
`index.html`. The icon (two bars with a dot between them) and the wordmark lockup (the name with a dot after
"Jobs", `src/components/Wordmark.tsx`) are separate drawings that share the dot. The PNG and ICO files are
generated, not drawn by hand, and the icon is drawn in one place, `markShapes()`; to change the mark or the
preview image, edit `scripts/generateBrandAssets.mjs` and run:

```bash
node scripts/generateBrandAssets.mjs     # needs Chrome; set CHROME_BIN if it is not in the usual place
```

It uses Node's own modules only and draws through headless Chrome, so the preview image carries the real
typefaces. `src/brandAssets.test.ts` checks the sizes of the files it writes.

## Security headers

`npm run build` also writes `dist/_headers`: a Content-Security-Policy and a few other response
headers, in the file format Cloudflare Pages (and Workers static assets) applies to the site it serves
from `dist`. Nothing needs configuring. The policy lets the browser contact exactly the API and
Supabase hosts the bundle was built against, so building for your own hosts gives you a policy for
your own hosts.

The build prints the whole file in its log (`vite build --logLevel warn` silences that), which is the
easiest way to copy it from a CI run. The file is plain text, one `Name: value` per line.

### What is in it

| Header | Value, and why |
| --- | --- |
| `Content-Security-Policy` | See the next table. |
| `X-Content-Type-Options` | `nosniff`. |
| `Referrer-Policy` | `strict-origin-when-cross-origin`. |
| `Permissions-Policy` | Camera, microphone, geolocation, payment, USB, serial, HID, Bluetooth, MIDI, motion and orientation sensors, screen capture, XR and clipboard read switched off. The app uses none of them, and only writes to the clipboard. |
| `X-Frame-Options` | `DENY`, the older twin of `frame-ancestors 'none'`. |
| `Cache-Control` | `no-cache` for the HTML, so a new deploy is picked up; `public, max-age=31536000, immutable` for `/assets/*`, whose file names carry a content hash. |

Not set here, on purpose: `Strict-Transport-Security` is a one-way commitment (browsers refuse plain
http for the whole domain, for as long as you say), so turn it on yourself, in your host's dashboard,
once https works everywhere on the domain. `Cross-Origin-Opener-Policy` and
`Cross-Origin-Embedder-Policy` can break cross-origin sign-in windows and embeds, and nothing here
needs them.

| Directive | Value | Why |
| --- | --- | --- |
| `default-src` | `'none'` | Anything not listed below is refused. |
| `script-src` | `'self'` | One same-origin bundle. No inline script, no `eval`, no `'unsafe-inline'`. |
| `style-src` | `'self'` and the SHA-256 of the inline `<style>` in `index.html` | Hashes are computed from the HTML the build emitted, so editing that block keeps working. React's `style={...}` props are applied through the CSSOM, which this directive does not govern, so no `'unsafe-inline'` and no `style-src-attr`. |
| `img-src`, `font-src` | `'self'` | The favicon and touch icons, and the three self-hosted typefaces (see "Fonts and brand files"). `build.assetsInlineLimit` is 0, so an image or font arrives as a file under `/assets/`, never as a `data:` URL. |
| `manifest-src` | `'self'` | The web app manifest, `site.webmanifest`. With `default-src 'none'` the browser would refuse to read it. |
| `connect-src` | the API origin and the Supabase origin | From `VITE_API_BASE_URL` and `VITE_SUPABASE_URL`: scheme, host and a non-default port, no path, no wildcard. Supabase is used for sign-in only, so no websocket origin is listed. The site's own origin is not listed either; nothing fetches it from script. |
| `frame-src` | `https://www.linkedin.com` | The click-to-load post embed in Hiring posts, the only frame the app ever creates. |
| `worker-src`, `object-src` | `'none'` | No workers, no plugins. |
| `base-uri`, `form-action` | `'self'` | |
| `frame-ancestors` | `'none'` | Nothing may frame the app. |
| `upgrade-insecure-requests` | | A stray `http://` subresource is fetched over https instead. |

Downloads of generated PDFs and exports are blobs saved through a link, which no CSP directive
governs, and Google sign-in is a top-level redirect through Supabase, which none governs either.

### How it is made

`scripts/securityHeaders.ts` builds the text, and a plugin in `vite.config.ts` emits it as part of the
bundle, in the `generateBundle` hook after Vite has written `index.html`. That is why it is a plugin
and not a step after `vite build`: it reads the same resolved environment Vite inlines into the
bundle (shell variables over `.env.local` over `.env`), so the policy cannot name different hosts than
the code calls; it follows `--outDir` and `--mode`; it hashes the HTML that was actually emitted; and
a build that fails writes nothing. Both addresses are checked before bundling starts, and a bad one
stops the build with a message that names the variable and never prints its value (the build log
is a CI log, and an address can carry a password or a token by mistake).

`--base` and `build.assetsDir` shape the cache rule, which is a path on the site's own host, so both
must be plain paths: a base such as `/` or `/app/`, and an assets directory such as `assets`. A base
on another origin (`https://cdn.example.com/` or `//cdn.example.com/`) is refused, because the policy
allows scripts and styles from the site's own origin only and would blank the page; a path with a
space, `:` or `*` in it, which `_headers` reads as something else, is refused too.

The generated file replaces any `web/public/_headers`. To add a rule, change the generator.

`vite dev` is untouched. `vite preview` serves the built files **without** applying `_headers`, so a
policy problem will not show up there; check the built output behind a server that applies the file
(a Cloudflare preview deployment, or Wrangler's local server for your project type).

### Other hosts

Cloudflare Pages reads `dist/_headers` as it is. Anywhere else, copy the policy out of that file (or
the build log) into your server's own syntax. Two things to carry over besides the values: unknown
paths must serve `index.html` (a single-page app), and the HTML must stay revalidated while
`/assets/*` is cached for a year. These sketches are untested; check them against your server's
documentation, in particular how it combines headers set at more than one level.

```nginx
location / {
    try_files $uri /index.html;
    add_header Content-Security-Policy "<the value from dist/_headers>" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "<the value from dist/_headers>" always;
    add_header X-Frame-Options "DENY" always;
    add_header Cache-Control "no-cache" always;
}
location /assets/ {
    # add_header in a block replaces what the enclosing block set, so repeat every header here.
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    # ... and the five security headers above ...
}
```

```caddy
example.com {
    root * /srv/between-jobs
    try_files {path} /index.html
    header {
        Content-Security-Policy "<the value from dist/_headers>"
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
        Permissions-Policy "<the value from dist/_headers>"
        X-Frame-Options DENY
        Cache-Control no-cache
    }
    @assets path /assets/*
    header @assets Cache-Control "public, max-age=31536000, immutable"
    file_server
}
```

Netlify also reads a `_headers` file from the publish directory in a similar format; check its
documentation for how overlapping rules and the `! Cache-Control` removal line are treated before
relying on it.

### When something is blocked

Open the browser console on the page that misbehaves. A blocked request says which directive refused
it and what it was, for example:

```
Connecting to 'https://api.other.example/' violates the following Content Security Policy directive:
"connect-src https://api.example.com https://project.supabase.co". The action has been blocked.
```

- **A `connect-src` refusal for your own API or Supabase host.** The build was made with different
  values than the ones in use. Fix `VITE_API_BASE_URL` or `VITE_SUPABASE_URL` in the build environment
  and build again; the policy only changes when you rebuild. Compare the origin in the message with
  the one in the `connect-src` line of `dist/_headers`: a different port, `http` instead of `https`
  and a `www.` prefix each count as a different origin.
- **A `connect-src` refusal for a `wss://` Supabase address.** Something now uses Supabase Realtime.
  Add the `wss://` origin to `connect-src` in `scripts/securityHeaders.ts`, with a test.
- **A refusal to run an inline script, or to apply an inline style.** Something put markup into the
  page that is not in the build, most often a hosting feature that injects a script (some CDN
  analytics and optimisation features do). A `<style>` block in `index.html` is hashed
  automatically. An inline `style="..."` attribute or an event-handler attribute (`onclick`,
  `onload`, ...) cannot be hashed, so the build itself refuses a page that has one and names the
  attribute: move the style to the stylesheet and the handler to a script module. An inline
  `<script>` in `index.html` is hashed, but prefer a module.
- **A `frame-src` refusal.** A new embed needs its origin added to `frame-src` in the generator.
- **To try a change without enforcing it,** rename the header to
  `Content-Security-Policy-Report-Only` in the file you deploy: violations are logged to the console
  and nothing is blocked.

If the browser seems to ignore the policy, check that the response carries it:
`curl -sI https://your-site.example/ | grep -i content-security`. Cloudflare Pages ignores a
`_headers` line longer than 2,000 characters, so the generator refuses to write one.
