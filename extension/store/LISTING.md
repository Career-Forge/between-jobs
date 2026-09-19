# Between Jobs browser extension -- Chrome Web Store listing pack

> **DRAFT -- review before submitting. Not legal advice.**
> Written 2026-09-19 from the extension's code (version 0.1.0) and the store's public
> documentation as it read that day. Every `[MAINTAINER TO FILL: ...]` marker needs a real
> value. Nothing here has been entered into the developer dashboard; no store account was
> touched.

Companion files: `PRIVACY.md` (the policy to host publicly) and `PERMISSIONS.md` (one
justification per permission, and the remote-code answer). This file is the rest of what
the dashboard asks for.

The dashboard's own screens are not reproduced in Chrome's public documentation, so
anything below about an exact checkbox label, category name or field limit that the
documentation does not state is marked **confirm in the dashboard**.

## 1. Store listing tab

| Field | Value | Notes |
|-------|-------|-------|
| Name | `Between Jobs` | Taken from the manifest `name`. Limit: 75 characters. |
| Summary | `Deterministic ATS autofill from your own prepared résumé and cover letter. You always click submit.` (99 characters) | Taken from the manifest `description` as I understand the dashboard (**confirm**). Limit: 132 characters. To change it, edit `manifest.description` in `extension/wxt.config.ts` and rebuild. A plainer alternative (121 characters): `Fill Lever, Greenhouse and Ashby job applications from your own Between Jobs résumé and profile. You always click submit.` "ATS" is jargon for some job seekers; the alternative names the three services instead. |
| Detailed description | Section 2 below (2,574 characters) | The docs give no hard limit. Start with what it does, as Chrome recommends. |
| Category | Productivity | **Confirm the subcategory in the dashboard.** A "Workflow & Planning" subcategory exists; I could not fetch the dashboard's own list. |
| Language | English | Description and screenshots can be localized later; the small and marquee tiles cannot. |
| Icon | 128x128 PNG | Present (`public/icon/128.png`), but it is a generic green puzzle-piece outline, which reads as a placeholder. Chrome's listing rules name "non-descriptive" metadata, including the icon, as grounds for rejection. Replace it with a real mark, with padding inside the canvas, before submitting. |
| Screenshots | 1 to 5 at 1280x800 | Section 6. At least one is required. |
| Small promo tile | 440x280 PNG or JPEG | The docs list it as required. Section 6. |
| Marquee promo tile | 1400x560 PNG or JPEG | Optional. |
| Promo video | YouTube link | Listed among the assets in the docs; **confirm in the dashboard** whether it is required (I believe it is optional). |
| Homepage URL | [MAINTAINER TO FILL] | The manifest currently declares `https://between-jobs.tech` as `homepage_url`. Confirm that is the page you want reviewers and users to land on. |
| Support URL | [MAINTAINER TO FILL: a page or address where users can get help] | Do not invent one. |
| Official URL | [MAINTAINER TO FILL: only if you verify the domain as a publisher] | Optional. |
| Mature content | No | Nothing in the extension is age-restricted. |

Listing rules that apply (Chrome's Listing Requirements page): the description, category,
icon and screenshots must be accurate and comprehensive; keyword spam (lists of brands,
or the same keyword repeated more than five times) is a violation; and anything in the
privacy fields that contradicts what the extension does gets it removed. The description
below names each of the three services twice and the product name four times, on purpose.

## 2. Detailed description (paste-ready)

Plain text; the store does not render markdown. Keep the trademark sentence.

```text
Between Jobs fills in job applications on Lever, Greenhouse and Ashby with the profile, résumé and cover letter you already prepared in your account. You review everything and you click submit -- the extension never does.

WHAT IT DOES
- Recognizes an application form on these sites' public job boards and checks whether it is a job you track in Between Jobs.
- Fills your name, email, phone, location and links into the form's plain text fields, and attaches the résumé (and the cover letter, where the form has a place for one) prepared for that job.
- Lists the screening questions it did not fill. For a free-text question you can press Draft answer: it first looks for an answer you saved before, and otherwise asks the AI provider you configured, with your own API key, for a first pass. You read and edit it, and nothing is written into the form until you press Fill.
- Never overwrites what you have already typed, unless you press Refill all.
- Once you have submitted on the employer's site, one button records the application as applied in your account.

WHAT IT NEVER DOES
- It never clicks Submit or Apply, never submits a form, and never ticks a checkbox, consent box or dropdown for you.
- It skips voluntary self-identification and accommodation questions (gender, race, disability, veteran status and similar): they are not filled, not offered for drafting and not sent anywhere.
- It has no analytics, no advertising and no tracking, and it does not sell your data.
- It runs only on those three services' job-board sites and on no other website.

WHAT YOU NEED
- A Between Jobs account, signed in to the extension with your email and password. Accounts that only use "Sign in with Google" cannot sign in to the extension yet.
- The job tracked in your account first, with a prepared résumé, so the extension can match the page to it.
- For drafted answers, an AI provider key saved in your settings.
- Chrome 148 or newer.

GOOD TO KNOW
- Application forms embedded in an employer's own website, and EU-hosted job boards, are not supported yet.
- Automatic filling is a convenience, not a guarantee. Check every field before you submit.
- Your data goes only to the service you sign in to and its sign-in provider. Answers you ask for are also sent, through that service, to the AI provider you chose. Full details: [MAINTAINER TO FILL: privacy policy URL]

Between Jobs is open source: [MAINTAINER TO FILL: repository URL]. Lever, Greenhouse and Ashby are trademarks of their respective owners; this extension is not affiliated with or endorsed by them.
```

Each sentence is backed by the code: the "Sign in with Google" line by the extension having
only `signInWithPassword` and the web app having no set-password flow; "EU-hosted job boards"
and embedded forms by the README's known limitations; "Chrome 148" by
`minimum_chrome_version`; "first pass" and "nothing is written until you press Fill" by
`handleDraftAnswer` and `handleFillAnswer` in `sidepanel/App.tsx`. If you change the
account or provider wording, change `PRIVACY.md` to match.

## 3. Privacy practices tab

### Single purpose description

> Help a signed-in Between Jobs user complete job applications on Lever, Greenhouse and
> Ashby: fill their own saved details and prepared documents into the form, help draft
> answers to screening questions for their review, and record in their account that they
> applied. The user always reviews and submits.

(303 characters. No limit is documented for this field.) Everything the extension does is
one of those verbs; nothing else (no job search, no tracking of browsing, no other sites) is
in it.

### Permission justifications

Paste from `PERMISSIONS.md`: one paragraph each for `storage`, `sidePanel`, and the four
host permissions (or the single combined paragraph if the dashboard shows one field).

### Remote code

Select **No, I am not using remote code.** The explanation, if a reviewer asks, is in
`PERMISSIONS.md`: no downloaded script is ever run; the only fetched artifact that steers
behavior is a signed JSON field map, verified with an Ed25519 public key carried in the
extension and interpreted by packaged code.

### Data usage -- which data types the extension handles

Chrome's Privacy practices tab has two groups of checkboxes: the data types the extension
collects, and certifications. The documentation says that disclosure is required even for
data handled only on the device, and that everything ticked must match the privacy policy;
a mismatch is a removal risk. The type names below are the ones the dashboard uses as I
know them; **confirm each label and its tooltip definition in the dashboard.**

| Data type | Tick? | Why, from the code | How it is used |
|-----------|-------|--------------------|----------------|
| Personally identifiable information | **Yes** | Sign-in sends the account's email address. The service returns the user's name, email address, phone number, city/region/country and profile links, and their résumé and cover-letter PDFs, which the extension holds in memory and writes into the form. Saved answers can contain whatever the user typed. | Core extension functionality |
| Health information | No | Disability, accommodation and similar self-identification questions are never listed, drafted, filled or sent (`lib/questionSafety.ts`, D6). The filter is wording-based, so this rests on it working. | -- |
| Financial and payment information | No | Nothing handles payment or account numbers. | -- |
| Authentication information | **Yes** | The account email and password are entered in the panel and sent to the sign-in provider; access and refresh tokens are kept in `chrome.storage.session` and the access token is sent to the Between Jobs API on every request. | Core extension functionality |
| Personal communications | No | No emails, texts or chat messages are read or sent. (The cover letter is a document attached to the form, covered under personally identifiable information.) | -- |
| Location | No | No geolocation API is used. The user's saved home city, region and country are profile data, covered above. Every HTTPS request reveals an IP address to the receiving server; the extension does not collect it. If your server logs it and you want to be conservative, tick this and say so in the policy. | -- |
| Web history | **Yes** (conservative) | On a detected application form the extension sends that page's address (origin and path, with no query string or fragment) to the Between Jobs API to find the matching tracked job. That is a URL the user has open, sent off-device. It happens only on four hosts, only when signed in, and only when a form is present. | Core extension functionality |
| User activity | No | No keystroke, click, mouse or scroll logging, and no network monitoring. Button presses in the panel cause only the requests listed in the policy. | -- |
| Website content | **Yes** | The text of a free-text screening question is sent to the service when the user presses "Draft answer" or "Fill & remember", and the user-approved answer text is sent when they press "Fill & remember". Nothing else on the page is sent. | Core extension functionality |

For every ticked type the usage option is **core extension functionality** only. Do not tick
analytics, developer communications, advertising or marketing, fraud or credit detection, or
personalization: the code does none of them.

### Data usage -- certifications

The dashboard asks the developer to certify three things, which cover: not selling or
transferring user data to third parties outside the approved use cases; not using or
transferring it for purposes unrelated to the extension's single purpose; and not using or
transferring it to determine creditworthiness or for lending. The documentation I could
fetch does not reproduce the exact wording, so **read the dashboard's text before ticking.**
What the code supports for each:

- **Selling or transferring outside approved use cases.** The only onward transfer is the
  Draft answer path: the Between Jobs service sends the question, a profile summary and the
  job description to the AI provider the user configured with their own key. That is
  necessary to provide the extension's single purpose (drafting an answer), which is one of
  the permitted transfers in Chrome's Limited Use rules, and it happens only when the user
  presses the button.
- **Unrelated purposes.** Data is used only for the features in the policy.
- **Creditworthiness or lending.** Not used.

Chrome's Limited Use rules also limit human access to user data (with explicit consent, for
security, to comply with law, or aggregated and anonymized) and prohibit using web browsing
activity except for a prominently described user-facing feature. The URL lookup is such a
feature, which is one more reason the in-product disclosure below matters. The policy's
"nobody reads your data" sentence is a commitment you must be able to keep.

### Privacy policy URL

[MAINTAINER TO FILL: public https URL of the hosted `PRIVACY.md`, with its notes section
removed]. It must be the same URL on the developer account page, and the disclosures above
must be consistent with it.

## 4. In-product disclosure and consent (needs a code change)

Chrome's User Data FAQ says the prominent disclosure and the consent must occur **inside
the extension's own interface**, that store-listing text does not satisfy the requirement,
and that the extension must ask the user to take a specific action clearly agreeing before
it collects or handles user data. The 2026 policy update (enforced from 2026-08-01) also
removed the "closely related to the single purpose" qualifier and requires notice of any
later change in data practices. The side panel today has a sign-in form and one footer line.
I treat this as required before submitting.

Draft copy for a first-run screen, shown before the sign-in form, with an explicit agree
action (button labels are suggestions):

> **Before you sign in**
>
> Between Jobs autofill works with your Between Jobs account. When you are signed in and
> open a job application on Lever, Greenhouse or Ashby, this extension will:
>
> - send the address of that page (without any query string) to the Between Jobs service
>   to find the job you track;
> - download your profile details (name, email, phone, location, links) and the résumé and
>   cover letter you prepared, and use them to fill the form when you press Fill;
> - only if you press Draft answer or Fill & remember, send that question's text to the
>   service, which may pass it, with a summary of your profile and the job description, to
>   the AI provider you configured with your own key, and save answers you choose to
>   remember.
>
> It never submits an application, never ticks a checkbox, and never fills
> self-identification questions. It has no analytics and sells nothing. Full policy:
> [MAINTAINER TO FILL: privacy policy URL]
>
> [ I understand and agree ]   [ Not now ]

Implementation notes for whoever writes it: the sign-in form should not render, and no
request other than the extension's own start-up should be made, until the user agrees;
"Not now" should leave the extension inert. Remembering the agreement needs a stored flag
(`storage.local` is the natural place), and that must be added to the storage table in
`PRIVACY.md` section 4. When a later release changes what is collected, the screen should
appear again with the change described. This copy describes only what the code does today.

## 5. Test instructions for reviewers (Test instructions tab)

Chrome's documentation says this tab is not required for publishing and is useful when an
item needs credentials. This extension does nothing signed out and only acts on a job the
account already tracks, so without it a reviewer sees a sign-in form and stops. Provide it.

Draft (the credentials go in the dashboard's credential fields, never into this repository):

```text
The extension is useful only when signed in, and only on the application form of a job the
account tracks. A test account and three tracked postings (one per supported service) are
prepared for review.

1. Use Chrome 148 or newer. Install the extension and click its toolbar icon to open the
   side panel.
2. Sign in with the credentials provided in this dashboard (email and password).
3. Open the Lever application form: [MAINTAINER TO FILL: exact URL of a tracked posting's
   apply form]. The panel shows "Application found". Press "Fill this page". Name, email
   and phone are filled and the test résumé is attached. Nothing is submitted and no
   button on the page is clicked; the extension has no code that can do either.
4. Repeat on [MAINTAINER TO FILL: Greenhouse URL] and [MAINTAINER TO FILL: Ashby URL].
5. Under "These need your own attention", press "Draft answer" on a free-text question.
   The panel shows a draft labelled "AI drafted -- review before filling". Editing it and
   pressing "Fill" writes it into that one empty text box. (This step calls the AI
   provider configured on the test account, using a spend-capped key.)
6. To see that nothing is sent in the background, open chrome://extensions, choose "Inspect
   views: service worker" for the extension, and also right-click inside the side panel and
   choose Inspect (sign-in happens in the panel, everything else in the service worker).
   Watch both Network tabs while doing steps 2 to 5. The only requests are to
   [MAINTAINER TO FILL: API origin] and [MAINTAINER TO FILL: sign-in origin].
```

What you must prepare for this to work (none of it is in the code):

- An email-and-password Between Jobs account (a Google-only account cannot sign in to the
  extension) with an activated profile.
- A tracked application for a real, currently open posting on each service, tracked from
  that posting's exact URL, with a prepared résumé and cover letter. Postings close; check
  each one on the day you submit and again if review takes a while. Creating your own test
  job board on each service avoids this, if each service allows it without payment (I did
  not check their terms).
- A spend-capped AI provider key saved on the test account, so "Draft answer" works.
- Demo data only: never a real person's profile.

## 6. Screenshots and promo tiles (shot list, not the images)

Use a demo profile with obviously fake details and, where you can, a job board you created
yourself on each service, so the images carry no real applicant data and no third party's
branding. 1280x800, at most five, in this order (captions in italics are suggestions):

1. **The panel ready to fill.** Signed in, Lever application form open beside the panel
   showing "Application found. Ready to fill known fields." and the "Fill this page"
   button. *Your prepared details, one click.*
2. **After filling.** The same form with name, email and phone filled and a résumé
   attached; the panel showing the filled-field count and "Résumé: attached" (and the cover
   letter line if the form has one). *Filled fields, attached documents. Submit is yours.*
3. **Questions left to you.** The "These need your own attention" list with a "Draft
   answer" button on a free-text question, and a "Answer this one yourself" note on a
   choice question. Have a self-identification section on the form that is visibly absent
   from the list. *Self-identification questions are never touched.*
4. **Reviewing a draft.** The drafted-answer card: the "AI drafted -- review before
   filling" badge, one grounding warning if you can produce one honestly, the editable
   text with its character count, and the Fill and "Fill & remember" buttons. *You read and
   edit before anything is written.*
5. **Another service.** A Greenhouse or Ashby form filled the same way, panel visible.
   *Lever, Greenhouse and Ashby.*

Small promo tile (440x280): the product name and a one-line promise ("Your applications,
filled. You click submit."), no screenshots inside it. Marquee (1400x560), if you make one:
the same composition wider. Do not put claims in any image that the description does not
make.

## 7. Distribution tab (decisions for the maintainer)

| Field | Options and note |
|-------|------------------|
| Visibility | Public, unlisted or private, as I know the dashboard (**confirm**). Unlisted lets you submit and share a link while the real-browser checks below finish. |
| Regions | All regions by default. The privacy policy has jurisdiction-specific blanks (EU, UK, California); fill them or restrict regions. |
| Pricing | Free. The extension has no payment code. |

## 8. Before you submit

Everything here needs a person; none of it is done.

- [ ] Developer account, and the account-page privacy policy URL, set up.
- [ ] Production origins decided. Build the zip with `npm run zip` from a clean checkout and
      the three `WXT_*` values in the shell (`README.md`, "Building for the store"); the zip
      step refuses a local or non-https address and an all-zero version.
- [ ] Privacy policy: fill every `[MAINTAINER TO FILL]`, delete the notes section, host it at
      a public https URL.
- [ ] In-product disclosure and consent screen (section 4) built and reflected in
      `PRIVACY.md`.
- [ ] Real logo replacing the placeholder icon; screenshots (section 6); small promo tile.
- [ ] Reviewer test account, tracked postings and spend-capped AI key (section 5).
- [ ] The real-browser verification the earlier phases could not do: E3b (Draft answer,
      Fill, Fill & remember against a live Greenhouse or Ashby page and a real AI provider
      key) and E3c (a signed Lever field map on a live Lever page), plus the E6 smoke checks:
      the panel recognized by `sender.url`/`sender.origin` in Chrome 148+, a Greenhouse or
      Ashby single-page navigation firing `wxt:locationchange`, and the review box sizing.
- [ ] Decision on `host_permissions` (redundant with the content-script matches today;
      `PERMISSIONS.md` note 1) and on the wildcard CORS the extension's API access depends on.
- [ ] Bump `version` in `package.json` for every upload.

## Sources (checked 2026-09-19)

Every fetch below succeeded; none had to be skipped. The MV3 review that preceded this pass
had already fetched the policy-update, troubleshooting, dashboard-privacy and review-process
pages.

- [Fill out the privacy fields](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy)
  -- single purpose, permission justifications, remote-code field, the two data-usage
  checkbox groups, policy URL consistency. It does not list the data types or the
  certification wording.
- [Complete your listing information](https://developer.chrome.com/docs/webstore/cws-dashboard-listing)
  -- icon 128x128; screenshots at least one 1280x800, up to five; small tile 440x280;
  marquee 1400x560; homepage, support and official URL fields. Title 75 and summary 132
  characters came from a search over Chrome's documentation, not from this page.
- [Listing Requirements](https://developer.chrome.com/docs/webstore/program-policies/listing-requirements)
  -- accurate metadata, keyword-spam rules, privacy fields must match behavior.
- [Limited Use](https://developer.chrome.com/docs/webstore/program-policies/limited-use)
  -- permitted transfers, human-access rules, web-browsing-activity restriction.
- [User Data FAQ](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq)
  -- in-product disclosure and affirmative consent, local handling still needs disclosure,
  privacy policy contents.
- [Disclosure Requirements](https://developer.chrome.com/docs/webstore/program-policies/disclosure-requirements)
  and [Privacy Policies](https://developer.chrome.com/docs/webstore/program-policies/privacy).
- [2026 policy updates](https://developer.chrome.com/blog/cws-policy-updates-2026)
  -- data collection strictly necessary to the single purpose; disclosure regardless of
  relation to the single purpose; notice of later changes; enforcement from 2026-08-01.
- [Review process](https://developer.chrome.com/docs/webstore/review-process) -- broad host
  patterns lengthen review; every permission must be needed; test instructions.
- [Provide test instructions](https://developer.chrome.com/docs/webstore/cws-dashboard-test-instructions)
  -- the tab exists for credentials and is not required for publishing.
- [storage API](https://developer.chrome.com/docs/extensions/reference/api/storage) --
  when `storage.session` and `storage.local` are cleared, and content-script access.

Not verifiable from public documentation, and therefore hedged above: the dashboard's exact
data-type labels and tooltips, the certification wording, the category and subcategory
list, whether the promo video is required, the visibility options, and any character limit
on the single-purpose and justification fields (none is documented).
