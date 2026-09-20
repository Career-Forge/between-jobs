# Hiring Signals golden input: `provider_responses/`

Search-provider response bodies for the P3 adapters and the per-application service,
and (P4) for the standalone Hiring signals tab.
Companion to [../inputs/README.md](../inputs/README.md), which holds the P2 parser's
sanitized corpus. Read that one's rules first -- they apply here too, and where this
file is stricter it says so.

## What is here, and what "real" means for each

| File | Shape | Content |
| --- | --- | --- |
| `firecrawl_company_posts.json` | real Firecrawl v2 `search` response | **composed by hand** (13 rows, below) |
| `firecrawl_role_posts.json` | real Firecrawl v2 `search` response | **composed by hand** (20 rows, P4, below) |
| `firecrawl_empty.json` | real Firecrawl answer with nothing to return | none to sanitize |
| `you_com_empty.json` | real You.com answer to a LinkedIn-directed query | query and uuid replaced |
| `you_com_unscoped_control.json` | real You.com answer to an ordinary (non-LinkedIn) query | rows composed, example.com / example.org |

The **shape** of every file is captured from a real provider response: field names,
nesting, the `N days ago ·` stamp and the ` ... ` elisions Firecrawl joins fragments
with, `results: {}` for a You.com answer with nothing in it. The **content** of the
three non-empty files was written by hand, on purpose, and that is stricter than the
P2 corpus (which is real text with identities replaced): a person's post body is
personal text, so no row here is a renamed copy of one. Where a row imitates a
kind of real post (a recruiter's pitch, a copy-pasted job share), it says the same
kind of thing in different words. `tests/golden/README.md` rule 1 ("never invent
parity fixtures by hand") is about parity against a reference implementation; there
is none for this feature, so there is no `expected/` directory either -- the tests
assert values decided by reading the rows, and the rows are documented below so a
reader can check each decision.

### `firecrawl_company_posts.json` -- one row per case

The set-piece is a search for a company called Stripe, role "software engineer",
"now" being `FIXTURE_NOW` (2026-09-19 18:00 UTC in `tests/hiring_signal_fakes.py`).
Every activity id is synthetic but **encodes a chosen post time** exactly (id =
`(milliseconds << 22) | sequence`, the layout the parser decodes), so a window test
gets the same verdict forever.

| # | Case | Age at `FIXTURE_NOW` |
| --- | --- | --- |
| 1 | company in the title, employee-authored, role matches | 50 h |
| 2 | company only in the OPENING of the text, generic title, role matches | 30 h |
| 3 | company in the opening, but a DIFFERENT role at it | 70 h |
| 4 | LinkedIn's auto-generated job-share post, authored by the company page (`ats_echo`) | 45 h |
| 5 | noise: company only inside a skills list, AFTER the opening | 100 h |
| 6 | noise: company only in a news line, AFTER the opening | 120 h |
| 7 | known limit: "ex-Company" inside the opening of a recruiter's pitch is kept | 60 h |
| 8 | off topic: a different company entirely | 40 h |
| 9 | a job seeker who names the company in the opening (`job_seeker`) | 20 h |
| 10 | too old for a week | 216 h |
| 11 | not a post: a `/jobs/view/` page (rejected) | -- |
| 12 | a post under a URN namespace the feature declines, `ugcPost` (rejected) | -- |
| 13 | the same post as row 2 under a second slug (a duplicate) | 30 h |

Person names in it are obviously synthetic (`Jordan Testwell`, `Avery Placeholder`,
...), handles carry a made-up suffix of the real shape, the one email is
`careers@example.com`. The company page handle in row 4 (`stripe`) and every company
and role name are public facts and stay.

### `firecrawl_role_posts.json` -- one row per case (P4, the standalone tab)

The set-piece is a search for a ROLE and a metro with no company at all: `software
engineer` in Bengaluru (India vocabulary), a 3-day window, "now" being `FIXTURE_NOW`.
Same rules as above: every row is written by hand (`gen_role_posts.py` in the private
scratch directory, not committed), every activity id is synthetic and encodes a chosen
post time, names are obviously synthetic, company names are the well-known fictional
sample companies (`Northwind`, `Contoso`, `Fabrikam`, `Litware`, ...), and the one
email is `recruiter@example.com`.

| # | Case | Age at `FIXTURE_NOW` | In the tab's search |
| --- | --- | --- | --- |
| 1 | a hiring manager, role in the title and the opening | 38 h | shown |
| 2 | a recruiter, India vocabulary (`immediate joiners`, `notice period`) | 30 h | shown |
| 3 | the role in the plural (`Software Engineers`) | 55 h | shown (a plural is accepted) |
| 4 | LinkedIn's auto job-share from a company page, a listing the registry tracks | 45 h | `echoes_hidden` in the registry world, shown (unknown) without one |
| 5 | an auto job-share whose company the registry has, but not this listing | 50 h | shown, `unmatched` in the registry world |
| 6 | an auto job-share from a company the registry does not know | 48 h | shown, registry match unknown |
| 7 | a different role entirely (`manual tester`) | 35 h | `role_mismatch_hidden` |
| 8 | a hiring post with no role of its own -- the role words come from a commenter's headline after the first elision | 52 h | shown, but UNVERIFIED: the role is not in the post's title or opening, so it ranks below every post that states the role (`tab_role_fit` says `later`); it is not hidden, because a genuine `Role: ...` line also sits after the opening |
| 9 | a job seeker (`open to work`) | 20 h | `job_seekers_hidden` |
| 10 | a real hiring post, four days old | 100 h | `too_old_hidden` for 3 days, shown for a week |
| 11 | not a post: a `/jobs/view/` page | -- | `rejected` |
| 12 | the same post as row 1 under a second slug | 38 h | `duplicates` |
| 13-17 | ONE job-alert account, five DISTINCT posts (the aggregator threshold); row 13 is fresher (4 h) than every other post in the set | 4 - 66 h | shown, `aggregator`, ranked BELOW every non-aggregator |
| 18 | someone offering referrals (`referral_offer`) | 57 h | shown |
| 19 | a walk-in drive with a date (`hiring_drive`) | 31 h | shown |
| 20 | a post url that names no author (`/posts/activity-<id>`) | 28 h | shown, no author, `aggregator` null |

For `software engineer` / `Bengaluru` / 3 days the buckets are: 20 raw = 1 rejected + 1
duplicate + 1 role mismatch + 1 too old + 1 job seeker + 0 echoes hidden + 15 shown (with
a registry that tracks row 4's listing: 1 echo hidden + 14 shown). The tests assert those,
and that `raw = shown + every hidden bucket`.

## Sanitization applied (and audited)

- Rows were composed by hand, never copied from a capture. An audit then found that
  three of them had drifted to within a phrase (seven or more consecutive words) of a
  real post's text, and they were reworded (see the audit below).
- Links inside text are absent or `example.com` / `example.org`; no `bit.ly`,
  `lnkd.in` or tracking token appears.
- Search-engine ids (`id`, `search_uuid`) are made-up UUIDs.

Audit, run against every new or modified file of this change, not only these four:

```
grep -c '@'                    -> 1 (careers@example.com), across the four fixtures
grep -c 'linkedin.com/in/'     -> 0
7+ digit runs                  -> activity ids (19 digits, synthetic), one 10-digit
                                  job-page id (`3999999999`, an obvious placeholder)
                                  and the zero-runs inside the made-up UUIDs
hosts in the fixtures          -> www.linkedin.com (post/job urls, never fetched),
                                  example.com, example.org, you.com (a favicon field
                                  of the real shape)
```

plus a scripted comparison with the raw captures (kept outside the repository: they
contain real names). It collected every author name (33) and profile handle (46) that
the parser can read out of the 102 captured results and searched all 43 new or
modified files for each -- **0 found**; and it looked for any run of 7 consecutive
words of a captured title or snippet -- **0 found** after the rewording above (at 5
words the only overlaps are stock phrases: the index's own `N days ago · We're`
stamp, `is looking for a`, a careers page's `<Company> Careers | Staff Software
Engineer` title). The denylist itself is personal data and is not committed.

## What the real captures showed

Two sets of real calls, both against the search endpoints only (no scrape, crawl,
map, extract or fetch; no `linkedin.com` url was requested by any means):

- **2026-09-19, 18 calls** (raw bodies kept privately): the per-application query
  for four real companies on both providers, five variants of the query on You.com,
  and the freshness probes below.
- **2026-09-20, 5 calls**, through the whole pipeline (route -> service -> real
  Firecrawl key -> parse -> filters), against a real database, then cleaned up.

The four companies were chosen for being different: a large, well-known company
whose name is also an ordinary word (A); a large one known by an acronym that is
also a ticker (B); a small one with a two-common-words name (C); a small, obscure
one (D).

### Firecrawl (`site:linkedin.com/posts`, 20 results asked, `qdr:w` unless noted)

| Company | Raw | Valid `/posts/` | Kept by the company filter | Role match among kept |
| --- | --- | --- | --- | --- |
| A, 09-19 | 20 | 20 | 13 | 9 |
| B, 09-19 | 18 | 18 | 11 | 6 |
| C, 09-19 | 10 | 10 | 0 | 0 |
| D, 09-19 | 0 | 0 | 0 | 0 |
| A, 09-20 (week / 3 days) | 20 / 14 | 20 / 14 | 15 / 8 | 13 / 7 |
| B, 09-20 | 20 | 20 | 10 | 5 |
| C, 09-20 | 10 | 10 | 0 | 0 |
| D, 09-20 | 1 | 1 | 1 | 0 |

- **113 results over the 9 queries; all 113 parsed to a valid post** (`rejected` 0):
  the `/posts/` scope holds, so almost nothing is wasted on non-posts.
- **58 of 113 (51%) named the company where it counts** (its title, its author, or
  the opening of the text -- `hiring_signal_relevance`). The other half is the index
  answering a company-scoped query with posts that merely contain its name late in
  the text, or none of it. Without the filter, C would have shown ten posts about
  other things as "hiring signals for" a company nobody was posting about.
- **3 of 9 queries ended with nothing to show** (C twice, D once) -- the honest empty
  state is a normal outcome for a small company, not an edge case.
- Latency 0.9 - 1.5 s per call. Cost: 4 credits for an answer of 11-20 results, 2 for
  up to ten, 0 for none -- the user's own credits, absorbed by the cache on a repeat click.
- **Freshness is honored by the provider.** For company A on 2026-09-19: `qdr:d` gave
  1 result (13.4 h old), `qdr:d3` gave 7 (the oldest 67.4 h), `qdr:w` gave 20 (the
  oldest 156.3 h). On 09-20, the 3-day query returned nothing the decoded post time
  called too old (`too_old_hidden` 0). The service still trims by the decoded time.

### You.com

- **Zero `linkedin.com` results on every LinkedIn-directed query**: nine queries (the
  per-application query for four companies; a bare `site:` path query, a domain-only
  `site:`, a month-wide window, a three-term query and a `linkedin` keyword in place
  of `site:` for one of them). Eight were a clean HTTP 200 with `"results": {}`, one a
  single non-LinkedIn page.
- Three unscoped **control** queries returned 45 pages between them, none from
  LinkedIn: the index answers, it just does not contain LinkedIn posts.
- Its `freshness` values were therefore checked on the control query, by the
  returned pages' `page_age`: `day` -> 5 pages, all dated the previous day; `week`
  -> 20 pages spanning 2026-09-12 .. 2026-09-19; a date range `2026-09-16to2026-09-19`
  -> 20 pages dated 09-16 .. 09-18. All three sat inside their windows.
- Consequence for the design: You.com is tried **after** the providers that can see
  LinkedIn, not first (`hiring_signal_search.PROVIDER_ORDER`; Serper, a Google proxy the
  design calls "fallback only", comes after even You.com). A user whose only key is
  You.com gets an empty answer, not an error -- and the page says that this provider's
  index does not appear to include LinkedIn posts, rather than presenting the empty list
  as a finding about the company.

### Company filter, measured

The design said: a hit is on-topic if the company appears anywhere in its title,
snippet, author or handle. Hand-labelling the 09-19 Firecrawl results for A and B
(38 posts: "is this a hiring post about this company?") against that plain rule:
precision 0.53, recall 1.00. The kept mistakes were stock-ticker posts, a hardware
spec sheet, skills lists (`... A, Paystack, ...`), an acquisition headline, a
candidate's own profile headline, and `ex-A` in a recruiter's pitch. The rule
shipped counts a mention only in the title, the author's name or handle, or the
**opening** of the text (the snippet up to its first ellipsis, at most 200
characters): **precision 0.71, recall 0.94** (17 true positives, 7 false, 1 missed).
The measurement is small (38 posts, two companies) -- it justifies the direction, not
a decimal. Known limits are listed in `hiring_signal_relevance.py`.

The author rule was tightened afterwards -- from "a word of the author's name or
handle is the company" to "the author's name or handle IS the company (or its
`Careers`/`Jobs` page)", and the author's own name is read out of the title first --
because a person whose surname is the company was shown as the company's hiring
signal. That was found with synthetic names, not the captures; **re-measured on the
same 38 posts it gives the same 17 / 7 / 1**.

### Not verified against the live API

**Brave and Serper.** No key for either exists in the account used, so both adapters
and their freshness parameters (`freshness=pd|pw`, `tbs=qdr:d|qdr:w`) are written from
the providers' public documentation and are covered by mocked-transport tests only.
Both ask for the next WIDER window where they cannot say "3 days", and the service
trims by the decoded post time, so a wrong provider window costs credits, never
correctness. **Neither was contacted at all.** The registry-echo path
(`ats_echo` posts matched against `job_registry_postings`) saw no echo in any live
result, so it is covered by the composed fixture and unit tests only; the registry
*read* was run against the real registry for six companies and returns candidates in
0.3 - 4.4 s.
