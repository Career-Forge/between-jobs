# Between Jobs extension -- permission justifications and remote-code answer

> **DRAFT -- review before submitting. Not legal advice.**
> Written from the final built manifest (`build/chrome-mv3/manifest.json`, rebuilt
> 2026-09-19, version 0.1.0) and the code it ships. Re-check it after any change to
> `wxt.config.ts` or to what the extension does.

This is for the **Privacy practices** tab of the Chrome Web Store developer dashboard.
The text in the quoted blocks is written to be pasted as-is. Everything outside them is
for you and for a reviewer who asks follow-up questions.

## The manifest, as built

| Key | Value |
|-----|-------|
| `manifest_version` | 3 |
| `name` / `version` | Between Jobs / 0.1.0 |
| `minimum_chrome_version` | 148 |
| `permissions` | `storage`, `sidePanel` |
| `host_permissions` | `https://jobs.lever.co/*`, `https://job-boards.greenhouse.io/*`, `https://boards.greenhouse.io/*`, `https://jobs.ashbyhq.com/*` |
| `content_scripts` | one script, `content-scripts/content.js`, on the same four `matches`; Chrome's defaults apply (top frame only, `document_idle`, isolated world) |
| `background` | service worker `background.js` |
| `side_panel` | `sidepanel.html` |
| `action` | `default_title` only (no popup; clicking the icon opens the side panel) |
| Not present | `optional_permissions`, `optional_host_permissions`, `externally_connectable`, `web_accessible_resources`, `content_security_policy` (Chrome's Manifest V3 default applies), `commands`, `omnibox`, `devtools_page` |

## Single purpose (for reference; the dashboard field is in `LISTING.md`)

Fill job application forms on Lever, Greenhouse and Ashby with the signed-in user's own
saved profile, résumé and cover letter, and help them draft answers to screening
questions for their review. Every permission below is used for that and nothing else.

## Permissions

### `storage`

> Keeps two small things on the user's own device and nothing else. chrome.storage.session
> holds the user's Between Jobs sign-in session (access and refresh tokens) so the
> extension can call its own service on their behalf; Chrome clears it when the browser
> restarts and does not expose it to content scripts by default. chrome.storage.local holds
> one whole number per supported site: the highest signed field-map version the extension
> has accepted, which is how it rejects a replayed older map after a browser restart.
> Profile details, résumés and page content are never stored.

Where: `lib/supabase.ts` (session adapter), `entrypoints/background.ts`
(`fieldMapVersion:<site>` keys). Chrome's permissions reference lists no install warning
for `storage`.

### `sidePanel`

> The extension's whole interface is a side panel, so it can sit beside the application
> form the user is filling in instead of covering it or closing on every click. The panel
> is where the user signs in, sees whether the current page is a job they track, presses
> Fill this page, reviews AI-drafted answers before anything is written into the form, and
> confirms I submitted this. The extension calls sidePanel.setPanelBehavior so that
> clicking its toolbar icon opens the panel.

Where: `entrypoints/sidepanel/`, `entrypoints/background.ts` (`setPanelBehavior`),
manifest `side_panel.default_path`. The panel is registered for every tab, so a user can
open it anywhere; off the four sites it only says it is not on a supported page and
touches nothing. Chrome's permissions reference lists no install warning for `sidePanel`.

## Host permissions

Each of these is the same access for the same reason: the content script runs there, on
the site's job-application pages, and the extension makes **no network request** to any of
them. Its own requests go to the Between Jobs API and sign-in origins, which are not listed
here: the service worker reaches them without a host permission only because those servers
answer cross-origin (CORS) requests, and the API currently allows any origin (see note 1
below). None of the four is a wildcard host and none is `<all_urls>`.

### `https://jobs.lever.co/*`

> Lever-hosted job pages. The extension's content script runs here to detect whether the
> page contains an application form and, only when the user presses a button in the side
> panel, to write the user's own profile details into the form's text fields and attach the
> résumé and cover letter they prepared. It never clicks, submits or ticks anything.

### `https://job-boards.greenhouse.io/*`

> Greenhouse's current job-board host. Same use as above: detect an application form and,
> on the user's button press, fill their own details and attach their own documents.

### `https://boards.greenhouse.io/*`

> Greenhouse's original job-board host. Boards here normally redirect to
> job-boards.greenhouse.io; it is listed so the extension also works on a board that is
> still served from this host. Same use as above.

### `https://jobs.ashbyhq.com/*`

> Ashby-hosted job pages, including the /application route where Ashby's form lives. Same
> use as above.

### If the dashboard shows one host-permission field, paste this instead

> These four exact hosts are the only sites the extension runs on; job application forms
> hosted by Lever, Greenhouse and Ashby live there. On them the content script checks
> whether the page has an application form and reads its screening questions so the side
> panel can list them. Only when the user presses a button does it fill their own profile
> details into text fields, attach their prepared résumé and cover letter, or write one
> reviewed answer into one empty text box. It never clicks or submits anything, never
> touches checkboxes, dropdowns or consent boxes, and never fills self-identification
> questions. It makes no request to these hosts and asks for no other site access.

Chrome shows a site-access warning at install for host access. I did not find the exact
warning string in Chrome's own documentation, so I have not quoted it.

## What is not requested, and why

| Not requested | Why the extension does not need it |
|---------------|------------------------------------|
| `activeTab`, `scripting` | The content script is declared in the manifest; nothing injects code at runtime. Neither is used anywhere in the source or the built bundles |
| `tabs` | The side panel asks Chrome for the active tab's numeric id (`tabs.query`) so it can message that tab's content script, and never reads a tab's URL or title |
| `cookies`, `webRequest`, `declarativeNetRequest`, `history`, `bookmarks`, `identity`, `notifications`, `alarms`, `downloads`, `clipboardRead`, `clipboardWrite`, `unlimitedStorage`, `offscreen` | No feature uses them |
| `<all_urls>`, `*://*/*`, any wildcard host | The extension runs on four named hosts and nowhere else |
| `optional_*` permissions, `externally_connectable`, `web_accessible_resources` | No web page or other extension can talk to it, and it exposes no resources to pages |

## Remote code

**Dashboard answer: No, I am not using remote code.**

Nothing in the package is downloaded and run. The reviewer-facing facts:

- All JavaScript is in the package: `background.js`, `content-scripts/content.js` and
  one side-panel chunk. A scan of those three built files finds no `eval`, `new
  Function`, `importScripts` or `import()` at all, and the extension's own source never
  creates a `<script>` element or sets a `src`. The manifest sets no
  `content_security_policy`, so Chrome's Manifest V3 default (which forbids remote code)
  applies. (The side-panel chunk does contain `createElement("script")` and `.src =`,
  three times each: that is React DOM's built-in resource-hoisting code for a `<script
  src>` a component would render. Nothing in this extension renders one. An automated
  scan may point at it; that is the answer.)
- **The one thing fetched at runtime that steers behavior is a signed field map**: a JSON
  data file, per supported site, served by the Between Jobs API at
  `GET /extension/field-maps/{lever|greenhouse|ashby}`. It is data interpreted by packaged
  code, never code:
  1. The map is written and **signed offline** by the maintainer with an Ed25519 private
     key that is kept outside the source repository and is never held by the running API
     (the API only serves rows that were already signed; see
     `scripts/sign_and_publish_ats_field_map.py`).
  2. The extension carries the **public key** in its own source
     (`KNOWN_PUBLIC_KEYS` in `lib/ats-field-map.ts`, currently one key id).
  3. The response contains the exact signed string (`payload_canonical`), its signature,
     and the key id. The service worker verifies the signature with WebCrypto Ed25519
     before parsing anything. An unknown key id, an unsupported schema or a bad signature
     means the map is refused.
  4. After the signature, the payload's own `ats_type`, `version` and `schema` must match
     the response **and the site the extension asked for**, and `version` must be a
     positive integer no lower than the highest version already accepted for that site
     (kept in `chrome.storage.local`), which blocks replay of an older, still-validly-signed
     map. Any failure **fails closed**: the site-specific parts are switched off. There is
     no fallback to a cached or bundled copy.
  5. What a verified map can contain is fixed by the packaged parser: CSS selector strings
     (passed to `querySelector`), an attribute-prefix string, one regular-expression string
     (compiled with `RegExp` and matched against label text), and a list of entries
     `{ field, selector, strategy, profileFields, separator }` where `strategy` is one of
     five names implemented in the extension (`direct`, `fallback`, `joinNonEmpty`,
     `firstNameWord`, `lastNameWord`). An unknown strategy, a wrong shape or an
     uncompilable pattern fails verification. Nothing is evaluated as code.
  6. Even a validly signed map cannot make the extension click anything or fill anything
     but text fields and file inputs: the write paths re-check each target's type
     themselves.
- What else is fetched at runtime is **the user's own data** (profile details, résumé and
  cover-letter PDFs) and **API responses** such as an AI-drafted answer. The AI call runs
  on the service, not in the extension. Chrome's own guidance treats fetching data or
  configuration, and calling a remote web service, as allowed; running downloaded logic
  is what is not.

## Notes for the maintainer

1. **`host_permissions` duplicates `content_scripts.matches`.** The current code needs
   the four hosts only for the content script, which the `matches` already covers, so the
   `host_permissions` entries are redundant. They were kept because removing them changes
   install-time behavior and needs a real-Chrome check first. The earlier recommendation
   stands: drop them after a smoke test. If you do, check what the dashboard then asks
   you to justify (I could not open the dashboard, so I do not know whether it derives
   the host list from content-script matches too); the paragraphs above stay accurate
   either way. If you later add the API origin to `host_permissions` to restrict CORS,
   add a paragraph for it: "the Between Jobs API, used only for the extension's own
   requests", and keep the privacy policy's section 3 consistent.
2. **`boards.greenhouse.io` is defensive.** Every board checked in earlier testing
   redirected to `job-boards.greenhouse.io`. The sentence in its paragraph says exactly
   that; keep it only while it is still true.
3. **Not covered:** EU-hosted Lever and Greenhouse boards (`jobs.eu.lever.co`,
   `job-boards.eu.greenhouse.io`) and Greenhouse forms embedded in a company's own site
   are not matched. Say so in support text rather than implying "all Lever boards".
4. **Optional hardening:** add a production-only `connect-src` CSP once the API and
   sign-in origins are fixed. Not needed for the remote-code answer.
5. **Minimum Chrome 148.** Chrome shows "not compatible" below it. The floor exists
   because every message listener returns a Promise (documented as supported from 148)
   and Ed25519 WebCrypto needs Chrome 137 or newer.
6. **Dashboard text limits.** I did not find a documented per-field character limit for
   the justification fields. Each paragraph above is under 700 characters.

### Sources (checked 2026-09-19)

[Chrome Web Store Privacy fields](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy),
[Permissions list](https://developer.chrome.com/docs/extensions/reference/permissions-list),
[Permission warnings](https://developer.chrome.com/docs/extensions/develop/concepts/permission-warnings),
[storage API](https://developer.chrome.com/docs/extensions/reference/api/storage),
[sidePanel API](https://developer.chrome.com/docs/extensions/reference/api/sidePanel),
[Improve extension security (remotely hosted code)](https://developer.chrome.com/docs/extensions/develop/migrate/improve-security),
[Review process](https://developer.chrome.com/docs/webstore/review-process),
[Transition to the browser namespace](https://developer.chrome.com/docs/extensions/develop/concepts/browser-namespace).
More in `LISTING.md`.
