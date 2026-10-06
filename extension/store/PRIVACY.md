# Between Jobs browser extension -- Privacy Policy

> **DRAFT -- review before submitting. Not legal advice.**
> Drafted from what the extension's code does at the commit it ships in. Every
> `[MAINTAINER TO FILL: ...]` marker needs a real value before this is published,
> and the "Notes for the maintainer" section at the very end must be deleted from
> the copy you host publicly.

Effective date: [MAINTAINER TO FILL]
Last updated: [MAINTAINER TO FILL]

## 1. The short version

The Between Jobs extension helps you fill in job application forms that are hosted
by Lever, Greenhouse and Ashby, using details from your own Between Jobs account.

- It only does anything on four websites: `jobs.lever.co`, `job-boards.greenhouse.io`,
  `boards.greenhouse.io` and `jobs.ashbyhq.com`. It cannot see any other site.
- It talks to exactly two places: the Between Jobs service you sign in to, and that
  service's sign-in provider (a Supabase project). If you use the "Draft answer" button,
  the Between Jobs service in turn sends your request to the AI provider you set up
  with your own API key. That is the complete list.
- It has no third-party analytics, no advertising, no crash reporting, no tracking, and
  it sells nothing. The one thing like usage data is a small report to the Between Jobs
  service itself after each fill: counts only -- which of the three sites, which
  application, how many fields it tried, how many it filled, and one word (ok, partial or
  failed). Never what is in a field, a question, an address or any page text (request 12).
- It does nothing until you have agreed to the disclosure screen in the side panel: until
  then it reads nothing from any page and sends nothing anywhere. If a later release
  changes the disclosure, it asks again, and stops again until you agree.
- You always submit. The extension never clicks a submit, apply, next, continue, sign-in
  or create-account button, never submits a form, never types a PIN or a verification code,
  never answers a CAPTCHA, and never ticks a checkbox or consent box for you.
- Voluntary self-identification questions (gender, race, disability and the like) are
  skipped: not shown for drafting, not filled, not sent anywhere.

The rest of this page gives the detail.

## 2. Who is responsible

The extension is published by [MAINTAINER TO FILL: legal name of the person or company
that publishes the extension and operates the Between Jobs service], referred to below as
"we". Contact: [MAINTAINER TO FILL: privacy contact email]. Postal address:
[MAINTAINER TO FILL: needed if you declare trader status in the Chrome Web Store].

The extension is one client of the Between Jobs service. What the service does with your
account data in general is described in its own policy: [MAINTAINER TO FILL: link to the
Between Jobs service privacy policy]. This page covers the extension and the data it
causes to move.

The Chrome Web Store version of the extension is built to talk to one service:
[MAINTAINER TO FILL: production API origin] for the Between Jobs API and
[MAINTAINER TO FILL: production sign-in origin -- the Supabase project URL] for sign-in.
The addresses are fixed when the extension is built and cannot be changed inside the
extension. If you run your own copy of Between Jobs (it is open source), build the
extension from source with your own addresses; then your data goes only to your own
service and to your own AI provider.

## 3. What leaves your browser

These are all the network requests the extension makes. There are no others.

| # | Request | Sent to | When it happens | What it carries |
|---|---------|---------|-----------------|-----------------|
| 1 | Sign in | Sign-in service | You press "Sign in" | The email address and password of your Between Jobs account |
| 2 | Refresh session | Sign-in service | Automatically, when you have agreed to the disclosure screen, you are signed in and the stored token has expired | Your refresh token |
| 3 | Sign out | Between Jobs API, then the sign-in service | You press "Sign out" | Your access token, asking the Between Jobs API to reject any of this extension's own access tokens issued before this moment; then your session token, asking the sign-in service to end this extension's session |
| 4 | Look up the page | Between Jobs API | You have agreed to the disclosure screen, you are signed in, and an application form is detected on a supported site, whether or not you track that job | The page address without any query string or fragment (for example `https://jobs.lever.co/acme/<posting-id>`), plus your access token |
| 5 | Get the field map | Between Jobs API | The page is a job you track | Nothing beyond the site name (`lever`, `greenhouse` or `ashby`) and your access token |
| 6 | Get your details | Between Jobs API | The page is a job you track | The application's id |
| 7 | Get your résumé and cover letter | Between Jobs API | The page is a job you track, and you press "Fill this page" for the first time on it | The application's id |
| 8 | Check for a saved answer | Between Jobs API | You press "Draft answer" | The question's text, lower-cased with whitespace collapsed; and, when fixed wording rules in the extension recognise the question as one of a few common kinds ("why do you want to work here", "how did you hear about us", a work-authorization question and the like), the name of that kind; and the country the job's posting names, when it names one |
| 9 | Draft an answer | Between Jobs API | You press "Draft answer" and no saved answer matched | The application's id and the question's text |
| 10 | Save an answer | Between Jobs API | You press "Fill & remember" | The question's text (normalized as above) and the answer text you approved; and the name of the kind of question, if it was recognised (for a work-authorization question, together with the job's country, and only when the posting names one) |
| 11 | Mark as applied | Between Jobs API | You press "I submitted this -- mark as applied" | The application's id, the new status "applied", and a random key that stops a retried click being recorded twice |
| 12 | Report a fill | Between Jobs API | A "Fill this page" or "Refill all" on a job you track finishes, and it tried at least one field (or stopped with an error) | The application's id, which site (`lever`, `greenhouse` or `ashby`), how many fields it tried, how many it filled, and one word: `ok`, `partial` or `failed`. No field names, no values, no page address and no page text |
| 13 | Count a saved answer's use | Between Jobs API | A saved answer is filled into a box exactly as it was stored | The saved answer's id |

Request 3's first call (to the Between Jobs API) and requests 4 to 13 also carry your
access token so the service knows which account they belong to; the extension makes that
first call before ending the local session, so the token is still readable. If it fails
(for example the service is unreachable), the extension still ends your local session --
your device is always what promptly stops it being used, and that call is a further
protection against a token that leaked or was left behind, not a condition for signing
out at all. Requests 5 and 6 happen as soon as the page is recognized as a job you
track, not when you press "Fill this page" -- request 7 does not: your résumé and cover
letter are only downloaded the first time you actually press "Fill this page" for that
application, not on every page visit, and the extension reuses what it already
downloaded for the rest of that page's life rather than asking again on a later Fill or
"Refill all". Requests 4, 5 and 6 happen again if you press "Try this page", or if the
page moves to a different posting without a full reload; request 7 would then happen
again too, the next time you press "Fill this page" on the newly-resolved application.
Request 12 happens at the end of each fill and is never waited for: if it fails, nothing
about the fill changes and nothing is shown. It is not sent if you have not agreed to the
disclosure screen, or have been asked to agree again and have not, when the fill ends.
Request 13 likewise only follows a fill that went in, and a failure is ignored.

**What you get back.** The service returns your profile details (name, email address,
phone number, city, region and country, and your LinkedIn, GitHub and portfolio links),
the country the job's own location text names (or nothing, when it names none -- a city
alone is not treated as a country), and the references the extension needs to download
your prepared résumé and cover letter as PDF files (the files themselves come from
request 7). The extension uses the profile details, the two files and the job's country.
The country is sent back only as part of requests 8 and 10, and the file references are
used only to download the two files. Nothing else in the response is kept. It also
receives the site's field map (see section 5).

**The AI provider.** "Draft answer" is the only feature that uses an AI model, and
Between Jobs has no AI provider of its own. You choose one in your Between Jobs
settings and supply your own API key (for example an OpenRouter key, or another
compatible endpoint). If you have not, "Draft answer" reports that setup is needed and
nothing is sent. When you press it and no saved answer matches, the Between Jobs service
(not the extension) calls that provider with your key. The key stays on the service and
is never sent to the extension. The request contains the question's text, a text summary
of your profile, and the job description of the job you track. The summary holds your
name, headline, city, region and country, and your summary bullets, recent roles,
projects, skills and education, cut off at 6,000 characters. It does not include your
email address, phone number or links, and it does not include the work-authorization text
in your profile. That text is added to a draft request only when the question itself is
about work authorization, a visa or sponsorship -- decided by fixed wording rules in the
Between Jobs service, not by the AI -- and then in its own labelled section; for any other
question, and for any question that looks like an instruction to the AI rather than a
question to you, it is left out. The question text and the job description are sent to the
AI wrapped as marked data it is told not to obey. A second
request checks the draft against those same materials and contains the draft too. The
provider's own terms and privacy policy apply to what it receives, and if it forwards
requests to a model vendor you selected (as OpenRouter does), that vendor is involved
too. Nothing is sent to an AI provider unless you press "Draft answer".

**The websites you apply on.** The extension sends nothing to Lever, Greenhouse or Ashby
itself. What you fill in reaches the employer's application system when you press that
site's own submit button. Be aware that once a value is in a form, the page can see it as
if you had typed it, and some sites start uploading an attached file, or save a draft,
before you submit.

**Technical data.** Like any network request, the ones above reveal your IP address, your
browser's user-agent string and the time to the servers that receive them. The sign-in
library adds its own header naming the library and its version (it identifies the
library, not you). We do not use any of this for tracking.

## 4. What stays on your device

| Where | What | How long |
|-------|------|----------|
| Chrome's `storage.session` area | Your sign-in session record: access token, refresh token, expiry, and the account record the sign-in service returns (your account id, email address and similar account fields) | Until you sign out, or until Chrome clears it -- which it does when the browser restarts and when the extension is disabled, reloaded or updated (so you sign in again after an update). By default Chrome does not let the extension's content scripts (the part that runs alongside web pages) read this area |
| Chrome's `storage.local` area | For each supported site that has a signed field map, one whole number: the highest signed field-map version the extension has accepted (used to reject an older map being replayed) | Until you uninstall the extension |
| Chrome's `storage.local` area | Whether you have agreed to the in-product disclosure screen shown before you can sign in, and which version of it you agreed to (a whole number, not just yes/no, so a later release that changes what this page collects can show the screen again instead of silently relying on an old agreement). The side panel writes it; the background worker and the part that runs alongside web pages read it (the latter reads nothing else from this area), and both re-read it before every request and before a fill starts writing, so a change takes effect at once, in tabs that are already open | Until you uninstall the extension, or until a future release changes what is collected and shows the screen again |
| Memory of the extension's script on the page you have open | The profile details, résumé and cover letter fetched for that tracked job, the job's country (when its posting names one), the references to the two files, and the field map | Only while that page is open. The extension stops using it as soon as you sign out, the page moves to a different job, or the stored agreement stops being valid for this version, and drops it from memory the next time the page is used, navigated or closed. An action already under way finishes what it is doing: a fill that has started writing finishes the page it is on and then stops, and reports nothing |
| Memory of the side panel | Drafts you are editing, and panel state | Until you close the panel or navigate away |

The extension's own code does not use cookies, `localStorage`, `sessionStorage` or
IndexedDB, and it never writes your profile details or your files to disk. One detail
about the sign-in library it bundles (Supabase's `supabase-js`): in the side panel, the
library writes a throwaway test entry to the panel's own local browser storage and removes
it straight away to check that storage works, and reads one debugging flag from it. It
keeps nothing about you there. Your session itself lives only in `storage.session`, as
above.

## 5. What the extension does on the page

**It reads** nothing from a page until you have agreed to the disclosure screen. After that it reads the page to decide whether it contains an application form, to find the
form's fields, and to read the text of the screening questions. It reads a field's
current contents only to check whether the field is empty, so it does not overwrite
what you have typed. Question text is shown in the side panel and is sent to the
service only when you press the buttons in the table above. It does not send page
content, form contents or anything you type.

**It writes** only when you press a button:

- "Fill this page" puts your profile details into plain text fields, and attaches your
  prepared résumé (and cover letter, where the form has a place for one) to the form's
  file-upload fields. It splits your one-line profile name into first and last name by its
  spelling; a part it cannot know (a one-word name has no last name; a name in a script
  whose word order the spelling does not show) is left empty and listed in the panel. On
  Greenhouse it also chooses your country in the form's country list, by typing the country
  name and picking the matching entry -- the one entry of a list it ever activates, only
  an exact match, and only a plain entry of that list (never a button, a link, a label or
  anything else a page dresses up as a list entry); if the list has no such country, or does not respond, the field is left
  as it was and the panel says so. It skips a field that already has text, a file or a
  choice unless you press "Refill all". It also triggers the ordinary input and change
  events, so the site notices the values.
- "Fill" on a drafted answer puts that one answer, which you have read and may have
  edited, into that one text box, and only if the box is empty.

**It never**:

- clicks, presses or activates any button, link or control on the page -- the one
  exception is picking an entry in Greenhouse's country list, above -- and never submits a
  form, including the site's submit, apply, next or continue buttons, or signs in, creates
  an account, types a PIN or verification code, or answers a CAPTCHA;
- ticks or changes any checkbox, radio button, date field or consent box, or any dropdown
  other than that country list;
- fills, lists for drafting, or sends the text of voluntary self-identification and
  accommodation questions (gender, sex, sexual orientation, race and ethnicity,
  disability and accommodation, veteran and military status, religion, age or date of
  birth, marital or family status, national origin, pregnancy and similar). It
  recognizes them by their wording, so an unusually worded question could be missed --
  review the form before you submit. Choice-type questions (dropdowns, yes/no and
  multiple choice), including any about work authorization, are always left to you. A
  free-text work-authorization or visa question is treated like any other free-text
  question: nothing is drafted until you press "Draft answer", and you review the draft
  first;
- runs any code it downloaded. See "Field maps" below.

The side panel also has an "I submitted this -- mark as applied" button. You press it
yourself, after you have submitted on the employer's site. It only updates your Between
Jobs record. It does not touch the employer's site, and the extension cannot tell whether
you actually submitted.

**Field maps.** For each supported site the service provides a small data file (a
"field map") that names which parts of that site's forms are which. It is data: page
selectors, a text pattern, and instructions chosen from a short fixed list that the
extension's own code carries out. It contains no program code. Each map is digitally signed
by us before it is published. The extension carries the matching public key and refuses a
map whose signature does not verify, whose contents do not match the site it asked for, or
that is older than one it has already accepted. When a map is refused, the extension turns
off the parts of that site's support that depend on it instead of using something else.

## 6. What is never collected

- No third-party analytics, crash reports, tracking or advertising code of any kind. The
  only report of how you use the extension is request 12, sent to the Between Jobs service
  itself: counts only, as described there.
- No browsing history. The extension runs only on the four sites above and does nothing
  on any other page. On those sites the only address it sends is that of a page with an
  application form, as described in request 4.
- No keystrokes, mouse movements, clicks or scrolling.
- No cookies. The extension does not read other tabs: its page script exists only on the
  four supported sites, and the side panel only learns which tab is active and when a tab
  finishes loading -- never its address, title or contents.
- No values you type into a form, and no field names or labels: the fill report holds
  numbers and one word.
- No provider API keys. They never reach the extension.

We do not sell your data, share it for advertising, or use it to decide credit or
lending. We use it only to run the features described here. Nobody at [MAINTAINER TO
FILL: operator] reads your data except with your permission, to keep the service
secure, to comply with the law, or in aggregated and anonymized form.

## 7. Retention and deletion

- **Your account data** (profile, applications, documents) stays for as long as your
  Between Jobs account exists and is governed by the service's own policy.
- **Saved answers.** If you press "Fill & remember", the question text (lower-cased) and
  your answer are saved to your account until they are deleted, together with the kind of
  question and the job's country when those were recognised, and, as the answer is reused,
  how many times and when it was last used (request 13). They are removed when
  your account is deleted. [MAINTAINER TO FILL: how a user asks for individual saved
  answers to be deleted.]
- **Application status.** Pressing "mark as applied" records that change in your
  application history.
- **Fill reports.** Each report (request 12) is kept as one product usage event on your
  account, which the Between Jobs service's own policy describes, and is removed when your
  account is deleted.
- **Drafts.** A draft you do not save with "Fill & remember" is not saved to your
  account. The AI provider you chose keeps whatever its own terms say.
- **Server logs.** The service's ordinary web-server and proxy logs may contain the
  requests in section 3, including the page address in request 4, for
  [MAINTAINER TO FILL: log retention period].
- **On your device**, see section 4. Uninstalling the extension removes everything it
  stored.
- **To delete your account and the data in it**, [MAINTAINER TO FILL: process and
  turnaround, for example an email address].

## 8. Security

- Every request goes over HTTPS. The extension's store-packaging step refuses a
  non-HTTPS or local service address.
- Your sign-in session is kept in Chrome's `storage.session` area, which by default Chrome
  does not let content scripts read, and the content script never receives your tokens.
- The content script, which runs next to a web page's own code, is the least-trusted part
  of the extension, so the extension's background worker checks who sent each message
  and validates it before acting on it.
- Signed field maps, as described in section 5.
- The extension is open source: [MAINTAINER TO FILL: source repository URL].

No system is perfectly secure, and we cannot promise that yours will never be
compromised.

## 9. Your choices

- Press "Sign out" in the side panel. That asks the sign-in service to end this
  extension's session and removes it from the browser, and separately asks the Between
  Jobs API to reject any of this extension's own access tokens issued before that moment
  -- so a token that leaked or was left behind stops working against the extension's own
  requests right away, rather than staying valid until it naturally expires. It does not
  sign you out of the Between Jobs website, and it has no effect on any session for that
  website itself: the two are scoped separately on purpose.
- Do not press "Draft answer" and nothing is sent to an AI provider.
- Do not press "Fill this page" or "Refill all" and no fill report is sent.
- Do not press "Fill & remember" and no answer is saved.
- Uninstall the extension to remove everything it stored on your device.
- Ask us to access, correct or delete your data: [MAINTAINER TO FILL: contact and
  jurisdiction-specific rights wording, for example for the EU, UK or California].

## 10. Children

The extension is not directed at children. [MAINTAINER TO FILL: minimum age.]

## 11. Changes to this policy

We will update the date at the top when this policy changes. If the extension's data
practices change in a way that affects you, we will say so inside the extension, before
the change takes effect, as well as here.

The extension's handling of user data follows the Chrome Web Store User Data Policy,
including its Limited Use requirements.

## 12. Contact

[MAINTAINER TO FILL: privacy contact email, and postal address if required.]

---

<!-- BEGIN NOTES FOR THE MAINTAINER -- delete everything from the horizontal rule above
     this line down to the end of the file before hosting this policy publicly. -->

## Notes for the maintainer

**Delete this whole section from the copy you host publicly.** Everything above is
derived from the code as it is at this commit; if the code changes, re-check the
document. Nothing here is legal advice, and nothing here was checked against the
Chrome Web Store dashboard's own live form.

### How the claims above were checked (2026-09-19)

- **Source scan.** `fetch(` appears only in `extension/lib/api.ts` (two functions, both
  calling `WXT_API_BASE_URL`) and in the Supabase client's `global.fetch` binding
  (`lib/supabase.ts`). No `XMLHttpRequest`, `WebSocket`, `sendBeacon`, `EventSource`,
  `document.cookie`, `chrome.cookies`, `chrome.history` or `chrome.webRequest` anywhere
  in `entrypoints/` or `lib/`, and no `localStorage`, `sessionStorage` or `indexedDB`
  in the extension's own code. The only `chrome.storage` uses are `lib/supabase.ts`
  (session area), the field-map version floor in `entrypoints/background.ts` (local
  area), and the consent flag in `lib/consent.ts` (local area; written by the side panel,
  read by the background worker and the content script, which also listens for changes to
  it). `XMLHttpRequest` appears once, in a comment in `lib/supabase.ts`. The side panel
  never imports `lib/api.ts`; every backend call goes through the service worker. `sidepanel.html` loads one local script and the CSS has no `url()` or
  `@import`, so no page load fetches anything remote.
- **Bundle scan.** A rebuild of the built bundles finds no analytics or telemetry
  hostname. Origins present as strings: the four site patterns, the two configured
  origins (from the build environment), `http://localhost:9999` (the sign-in library's
  own unused default constant), XML-namespace URIs, React's error-page URL and
  documentation links inside Supabase console-warning strings, none of which is
  requested. The word "Sentry" appears only inside one Supabase console-warning string per
  bundle, about trace-context headers; it is not an integration.
- **Runtime recording (this pass).** A scratch test (deleted afterwards) ran the real
  Supabase client, configured exactly as `getSupabaseClient()` configures it, under
  jsdom with `fetch`, Web Storage, `document.cookie` and `chrome.storage.session`
  instrumented. Network: exactly `POST /auth/v1/token?grant_type=password`, `POST
  /auth/v1/token?grant_type=refresh_token` (the just-in-time refresh `getSession()`
  performs when the stored token has expired) and `POST /auth/v1/logout?scope=local`;
  headers were `Authorization`, `Content-Type`, `X-Client-Info`,
  `X-Supabase-Api-Version` and `apikey`. No cookies. `chrome.storage.session` held one
  key after sign-in and none after sign-out. **New finding:** in a window context the
  library also touched `localStorage` -- a write-then-remove of a random `lswt-...` probe
  key, and a read of `supabase.gotrue-js.locks.debug`. Section 4 now says so instead of
  claiming the bundle uses no `localStorage` at all. This was recorded under jsdom, not
  in real Chrome; auth-js 2.116.0 also opens a `BroadcastChannel` (a same-origin message
  channel between the extension's own pages, no network) that jsdom did not exercise.
- **Human-submits.** No `.submit()`, `requestSubmit`, keyboard or mouse event, or `.checked`
  write exists in the extension source, and exactly one `.click()` does: activating an
  entry of Greenhouse's country list after the country name has been typed into it
  (`activateListboxOption` in `lib/greenhouse.ts`, which activates only a plain list entry
  inside a listbox -- every element from the option up to its listbox a plain layout
  element, and neither it nor anything around it a button, a link, a label, a form control
  or an element with such a role; a page that marks a submit button or a link as an
  "option" gets no click; `.focus()` is likewise called only on that list's own input). The only events dispatched on page elements are `input` and `change`,
  on text-entry elements, file inputs and that list's `<select>` or search box.
  `tests/neverSubmit.test.ts` scans the source (comments excluded) for any submit, next,
  continue, sign-in, create-account, PIN, verification-code, CAPTCHA or consent selector,
  click, submit, keyboard or mouse event, ticked box or navigation -- and fails on a
  planted one -- and runs the real content script against synthetic pages full of such
  controls (including a hostile country list) with the DOM's click, submit, dispatch and
  focus entry points watched. The scan is a tripwire for the ordinary spellings of those
  things (property and element access, `.call`, destructuring, assignment operators,
  `setAttribute`, `defineProperty`); the behavioural run is the net for obfuscated ones.
  Building a name out of pieces at run time is out of scope for static analysis, and
  navigation is checked statically only. The signed-map loader refuses a map whose selector
  matches a list of control names and spellings; that is a best-effort lock, not a proof (an
  opaque selector passes it). What guarantees nothing is submitted, ticked or typed into a
  code box is the engine's shape: it writes only profile text into text-entry controls, has
  no click path but the country list above, and has no source for a PIN or a code. None of
  this has been run against a live page or in real Chrome. Standard-field fill, per-question fill and file
  attach each re-check the target's type.
- **Sign-out.** `signOut({ scope: "local" })` calls `/logout?scope=local` (confirmed
  again in the recording above), so the refresh token is revoked server-side, and the
  local session is removed even if that call errors.
- **Sign-out, server-side revocation (2026-09-22 update, E6 continuation).** A prior pass
  of this policy said the backend verifies JWTs locally with no way to reject one issued
  before sign-out -- that gap is now closed, scoped to the extension only:
  `handleSignOut` (side panel) now sends `SIGN_OUT` to the background worker BEFORE
  calling `signOut()`, while the bearer token is still readable, and the background
  worker calls `POST /extension/sign-out`. The backend records that moment
  (`extension_sign_outs`) and `require_active_extension_user_id` -- the dependency every
  `/extension/*` route uses -- rejects any token whose own `iat` predates it. Confirmed
  by reading the source path end to end (`App.tsx` -> `background.ts` -> `extension_
  routes.sign_out` -> `extension_auth.record_extension_sign_out`/`require_active_
  extension_user_id`) and by the backend's own real, passing test suite (`tests/
  test_extension_auth.py`), which drives the actual dependency function against real
  signed JWTs. It is best-effort and non-blocking by design: a failed or unreachable call
  never prevents the local sign-out above, so this narrows the exposure window for a
  leaked or leftover token rather than being a precondition for signing out at all. It
  has no effect on the Between Jobs website's own session (a different auth path,
  `require_user_id`, never reads this table -- also proven directly in that same test
  file). Per this task's own hard rule against driving the MV3 extension through browser
  automation tooling, this was NOT re-confirmed by loading the built extension into a
  real Chrome and watching the network tab the way the rest of this section's findings
  were -- that check is the maintainer's own, separately.
- **AI provider.** `credential_resolver.resolve` has no platform default provider or key
  (its docstring: no hosted credits, "no silent platform fallback"); an unconfigured user
  gets a setup-required error. `llm_client.generate` falls back to OpenRouter's URL only
  when the user's own credential has no `base_url`. The profile summary cap (6,000
  characters) is in `job_fit_scoring.summarize_profile`. The first draft of this policy
  said "by default the provider is OpenRouter"; that was wrong and is corrected above.
- **Chrome storage semantics** (`storage.session` cleared on restart, disable, reload and
  update; not exposed to content scripts by default; `storage.local` cleared on removal)
  were re-read in Chrome's storage API reference on 2026-09-19.

### Things this draft cannot honestly say yet, or that need a decision

1. **In-product disclosure and consent screen -- SHIPPED (E6 continuation).** `App.tsx`'s
   `ConsentGate` now renders before the sign-in form (and before anything else) until the
   person presses "I understand and agree," which stores a version-carrying flag in
   `chrome.storage.local` (this section's own table, above) and only then lets
   `refreshDetection`/the tab-change listeners run at all. The content script and the
   background worker check the same flag themselves (`lib/consent.ts`, the one decision
   point), so a page that was already open reads nothing and sends nothing until the flag is
   valid, and stops again the moment it is removed or its version changes (a request or a
   fill already under way finishes first; the flag is re-read before each request and before
   a fill starts writing) -- confirmed by toolchain tests,
   not yet by a real Chrome session (see the E6-continuation session notes for exactly
   what that leaves unverified). The copy is `LISTING.md` section 4's own draft, close to
   verbatim; a live re-check of Chrome's current policy pages (2026-09-21) found nothing
   materially wrong with it. The privacy-policy link inside the screen is still the
   `[MAINTAINER TO FILL]` placeholder below -- fill it in before submitting. The panel builds
   its sign-in client only after agreement: constructing the client is itself a network
   request when a stored session has expired (the library refreshes it), so building it on
   mount would send a refresh token before the person had agreed.
2. **No self-service deletion.** No route or screen deletes saved answers
   (`approved_answers`), applications or the account. The table cascades on account
   deletion at the database level, and has an owner-only delete policy, but nothing calls
   it. Sections 7 and 9 therefore promise a contact-based process; you must be able to
   keep that promise.
3. **Server-side facts the code cannot tell me:** log retention, backups, the hosting
   provider, the sub-processors (at least Supabase for sign-in and storage, the
   `latex-service` that compiles the résumé and cover-letter PDFs, and the user's own AI
   provider), the access-token lifetime, and whether anyone at the operator can
   technically read user rows. The "nobody reads your data", "we do not sell it" and "we
   do not use technical data for tracking" sentences (sections 3 and 6) are commitments,
   not something the code enforces; make sure you can keep them.
4. **What the AI provider receives is broader than the question.** `summarize_profile`
   includes the candidate's name, headline and location, but no longer the free-text
   `work_authorization` field: that is added to a draft request only when a deterministic
   classifier (`application_answer_generator.is_work_authorization_question`) says the
   question itself is about work authorization or sponsorship, and never for text that is
   long, instruction-shaped or contains markup. Work-authorization and visa questions are
   deliberately outside the self-identification exclusion, so a free-text one can be
   drafted. That is a maintainer decision already on record; the policy states it plainly
   instead of hiding it.
5. **What the extension receives is what it uses.** `GET .../extension-payload` returns
   `prepare_result` trimmed to `{resume, cover_letter}` (references to the two files),
   `personal_info` and `job_jurisdiction`. It no longer returns the stored fit assessment,
   gate outcome, ATS attempts or warnings. The extension uses all three: the references to
   download the files, the profile details to fill, and the country to tag and look up
   remembered answers. Section 3 says so.
6. **The page address travels in a GET query string** (`/extension/lookup?url=...`), so
   it can land in access logs. The extension already strips query strings and
   fragments; the path still identifies the company and posting. Moving it to a POST
   body is on the earlier decision list.
7. **Request 7 (the PDFs) moved to "Fill" -- SHIPPED (E6 continuation).** Requests 5 and 6
   still happen on detection (your profile details and the field map are needed to even
   show what Fill would do), but request 7 -- the résumé/cover-letter download -- no
   longer does; it fires the first time you actually press "Fill this page" for that
   application, and is cached in the page's own memory for the rest of that page's life
   rather than re-fetched on a later Fill or "Refill all". Section 3's table above
   reflects this.
8. **Google-only accounts cannot use the extension.** The web app offers "sign in with
   Google" and email-and-password sign-up; the extension has only email and password, and
   the web app has no password-reset or set-password flow (no `resetPasswordForEmail` or
   `updateUser` call). Say so in support material or add a way in.
9. **Every extension update signs users out**, because Chrome clears `storage.session`
   on update. Section 4 says so; you may want that in release notes.
10. **The store build cannot be pointed at a self-hosted service.** Addresses are
    compile-time (`WXT_*`). Section 2 says self-hosters build their own copy. The root
    `README.md` says self-hosting means data "never leaves your machine"; that is not
    true for the AI-drafting path (the AI provider is remote), so avoid repeating it.
11. **File attach can cause an early upload.** Lever's own page showed its upload-success
    indicator right after the extension attached a file during earlier live testing, so
    the site processes the file at attach time. Section 3 says sites may upload before
    submit; I have not checked what Greenhouse and Ashby do.
12. **The self-identification filter is heuristic** (wording-based, deliberately
    over-inclusive). Section 5 says so rather than promising it cannot miss.
13. **Jurisdiction-specific content is left blank on purpose** (GDPR or UK GDPR lawful
    basis and rights, CCPA, minimum age). This draft is not a substitute for advice.
14. **A verbatim sentence in Chrome's Limited Use page** is aimed at products that use
    "Google APIs" user data. The extension uses no Google account data, so I wrote an
    affirmation in my own words at the end of section 11. Check whether you want the
    exact wording as well.
15. **The Supabase publishable key is inside the bundle.** That is by design (it is a
    public client key, and row-level security plus the API's JWT check are what protect
    data), but a reviewer or scanner may flag it; expect the question.
16. **Counts-only usage report (request 12).** The extension now sends one report per
    fill. It is the first thing in the extension that is usage data rather than a feature
    request, so `CONSENT_VERSION` was raised to 2: everyone who agreed to the earlier
    wording (which promised "no analytics" outright) is asked again. The Chrome Web
    Store's Privacy practices answers in `LISTING.md` section 3 were re-checked for it.
    Whether `application_id` should travel with the report (it ties the event to an
    application on the service) is a judgment call; the service accepts it as optional.
    Version 2 also covers what the screen now lists beyond the counts: the application's id
    on the report, the saved-answer use report (request 13), and the kind and country that
    travel with a question's text (requests 8 and 10).

### Sources (checked 2026-09-19)

See the sources list at the end of `LISTING.md`. The ones this document leans on:
[User Data FAQ](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq),
[Limited Use](https://developer.chrome.com/docs/webstore/program-policies/limited-use),
[Privacy Policies](https://developer.chrome.com/docs/webstore/program-policies/privacy),
[Disclosure Requirements](https://developer.chrome.com/docs/webstore/program-policies/disclosure-requirements),
[2026 policy updates](https://developer.chrome.com/blog/cws-policy-updates-2026) and
[storage API reference](https://developer.chrome.com/docs/extensions/reference/api/storage).
