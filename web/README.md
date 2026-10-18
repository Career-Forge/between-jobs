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
first tab stop is a "Skip to content" link to it. The command palette and the assistant drawer are reserved
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
  `components/paper/StateViews.tsx` (loading, empty, partial, stale, setup-required, failure).
- Their styles are in `src/styles/paper.css`, which defines no color of its own.

Tests that need a DOM (events, focus) start with `// @vitest-environment jsdom` and use
`src/testing/render.tsx`. `src/testing/a11y.ts` runs axe-core over a rendered container and fails on a
serious or critical violation; jsdom has no layout, so it cannot check color contrast (`tokens.test.ts` does
that for the token pairs).

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
