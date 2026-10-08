# Between Jobs

An open-source, bring-your-own-keys job-search workbench: find roles, track applications,
prepare application documents, practice interviews. This repository is the open-source
core of Between Jobs. A hosted version at [between-jobs.tech](https://between-jobs.tech)
is coming soon; it is not live yet.

**Status: pre-alpha.** The API, the web app and the browser extension all run from this
repository. One piece does not: resume and cover-letter generation needs a separate
engine service that is not part of this repository yet. Read "What works today" before
you start.

## Principles

- **BYOK-first.** You supply your own LLM and search keys; the platform has no shared
  key behind it. The scoring and research prompts shipped here are generic ones.
- **The human always performs the submit.** The platform drafts and prepares; it never
  submits an application or sends an email for you. The browser extension fills a form
  and stops. Gmail integration creates drafts and never sends; it also asks for read
  access, so that a background worker can read the replies to those drafts (see "What
  works today").
- **Your data lives in your own Supabase project.** Resume content, job descriptions and
  similar text are also sent to the LLM and search providers whose keys you supply, and
  if you connect Gmail, the text of replies to the drafts the app created also goes to
  your LLM provider. There are no third-party analytics or tracking SDKs in this repository.

## What works today

Running the API against a Supabase project, with an [OpenRouter](https://openrouter.ai)
key saved in the web app (Integrations page), these work. Where a feature also needs a
search or scraping key, it says so:

- **Sign-in and your profile.** Email and password through Supabase Auth (Google sign-in
  if you enable that provider). Your resume is imported as structured JSON in a fixed
  template; it is validated deterministically, versioned, and one version is active.
- **Applications.** Add a posting by pasting it, or from a URL (a posting already in the
  job registry needs nothing; any other URL needs a Firecrawl key). Track it on a board or
  list through the stages saved, applied, screening, interviewing, offer, rejected and
  withdrawn.
- **Discover.** Search the job registry (see "Self-hosting the job registry") and the
  live-search providers you have keys for, merge and de-duplicate the results, check that
  live-search links are still alive, and score fit against your profile with one batched
  LLM call. Saved searches run in the background and put strong new matches on your
  Today feed.
- **Company research and outreach support.** Research a company with cited sources and
  find named contacts (both need a You.com or Firecrawl key; Apollo, Hunter and Exa keys
  are optional enrichment), and draft outreach.
- **Gmail (optional).** Creates drafts in your own Gmail and never sends. It asks Google
  for `gmail.compose` and `gmail.readonly`. A background reply checker (on by default;
  set `DISABLE_GMAIL_REPLY_CHECKER` to turn it off) reads the thread of each draft the
  app created, to see whether you sent it and whether anyone replied. It has your LLM key
  classify the newest reply, moves that application to the matching stage when it is
  confident (0.85 or above), and queues anything else it proposes for your review on the
  Today feed.
- **Hiring signals.** Hiring-intent posts found through search-index results; needs a
  Brave, Serper, Firecrawl or You.com key saved on the Integrations page. The code never
  fetches linkedin.com itself.
- **Telegram bridge (optional).** Import a resume, paste a posting, list applications,
  and receive a message when a saved search finds a strong match. A resume generation is one
  message the bot edits as the work advances. `/privacy` summarises what is stored and who
  handles it, and answers every chat (it reads no data; a chat with no web account gets the
  wording that applies to it). `/learn` walks the first-run checklist and answers only a chat
  that is linked to a web account.
- **Discord bridge (optional).** The same bot on Discord, as slash commands in a direct
  message with the app: link an account, paste a job, list applications, generate a resume
  for one and get the PDF back, import a resume file, and receive the strong-match alert.
  Discord only delivers slash commands and button taps to an app like this, never text
  typed in the chat: see "Discord" under "Set it up".
- **Browser extension (Chrome).** On the Lever, Greenhouse and Ashby application page of a
  posting you are tracking, it fills your contact fields from your profile and drafts
  answers to custom questions with your own key. It never clicks submit.

### What needs an engine service that is not in this repository yet

Resume and cover-letter generation is done by a separate HTTP service, called
`forge-engines` in the code. **It does not exist in this repository yet, so generation
does not work from a clean checkout.** Until a public engine lands, these features fail
with a retryable `PROVIDER_UNAVAILABLE` error ("Couldn't reach the resume engine"):

- generating a resume and cover letter for an application (`POST /applications/{id}/prepare`,
  and the Telegram and Discord bots' "Generate resume" button and "apply to #N" (`/apply` on
  Discord), which run the same preparation), and with them everything that works from the generated documents -- PDF
  download, the export checklist, and attaching a resume or cover letter in the extension;
- the Tailor panel's coverage analysis, the gap interview, and the resume header preview;
- interview practice (it builds its context through the engine's ingest step).

The API reaches the engine at `FORGE_ENGINES_BASE_URL` (default `http://localhost:5682`).
The HTTP client, with every endpoint it calls (`/apply`, `/step0`, `/gap-interview`,
`/gap-interview/draft`, `/ingest`, `/personal`, `/header/resolve`), is
`src/between_jobs/api/forge_engines_client.py`, and the response types are in
`src/between_jobs/api/engine_contract.py`. The protocols in `src/between_jobs/engines/`
are the seam a bring-your-own engine is meant to implement; nothing implements them yet,
and the HTTP client does not go through them today. PDF output also needs the LaTeX
service in `latex-service/` (see its README).

## Repository layout

| Path | What it is |
| --- | --- |
| `src/between_jobs/api/` | The FastAPI service: routes, stores, background workers, provider clients |
| `src/between_jobs/engines/` | Protocols for a resume or score engine (no implementation yet) |
| `web/` | The web app: React and TypeScript, a thin client over the API |
| `extension/` | The Chrome extension (WXT, React): ATS autofill |
| `latex-service/` | A small, separate service that compiles LaTeX to PDF |
| `supabase/migrations/` | The database schema, as migration files |
| `scripts/` | Operator scripts, run by hand (reference-data imports, registry sampling, Telegram webhook and Discord command registration, account deletion) |
| `tests/` | The Python test suite |

## Set it up

You need Python 3.12 or newer, Node 22.22.2 or newer on the 22 line, 24.15 or newer on the
24 line, or 26 or newer (CI uses Node 24; other releases, such as 23 or 25, make `npm ci`
in `extension/` print engine warnings), Docker, and the
[Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started).

Steps 2 and 3 each start a server that keeps its terminal, so run each in its own
terminal, and step 4 in a third (or after stopping the dev server). Open each one in the
repository root: every `cd` below starts from there.

### 1. The database (Supabase)

Everything the API stores lives in Supabase: auth, Postgres, storage. The schema is the
files in `supabase/migrations/`, and **migrations are applied with the Supabase CLI,
never by running SQL in the dashboard** (SQL run there records no migration version,
which breaks the repository's migration drift check).

A local stack, which needs Docker and is the easiest way to try it:

```bash
supabase start                 # starts the stack; a first start applies every migration
supabase status -o env         # prints API_URL, SERVICE_ROLE_KEY, PUBLISHABLE_KEY, ...
```

(On a local stack that already holds data from an older checkout, `supabase db reset
--local` replays all the migrations, and erases that local data.)

A hosted project instead: create one in the Supabase dashboard, then

```bash
supabase link --project-ref <your-project-ref>
supabase db push --dry-run --linked    # read what would run
supabase db push --linked
```

(These are the commands `CLAUDE.md` and the CI workflows use; they have not yet been run
against a hosted project from this page.)

Do not skip the migrations: until they are applied the API's background workers report
`lease_unknown` and `/health` answers 503, on purpose.

### 2. The API

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # ".[dev]" adds the test tools; plain "." is enough to run it
cp .env.example .env           # set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
uvicorn between_jobs.api.app:app --reload --port 8012   # keeps this terminal; leave it running
```

From another terminal, check that it is up:

```bash
curl localhost:8012/health
```

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are the only required variables (from
`supabase status -o env` for a local stack, or the dashboard's Project Settings -> API for
a hosted project). `.env.example` lists every variable the API reads, with its default.
Port 8012 is what the web app's dev proxy and the extension's example setting expect.
The API starts its background workers in its own process; see "One container, one process"
below before running more than one copy.

### 3. The web app

```bash
cd web
npm ci
cp .env.example .env.local     # set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY
npm run dev                    # http://localhost:5173
```

The dev server forwards `/api/*` to the API on `localhost:8012`, so leave
`VITE_API_BASE_URL` unset while developing. A production build (`npm run build`) refuses to
run without `VITE_API_BASE_URL` set to the API's public `https://` origin; `web/.env.example`
explains why. Create an account on the sign-in page, open Integrations and save an
OpenRouter key, then import your resume JSON on the Profile page.

### 4. The browser extension

```bash
cd extension
npm ci
cp .env.example .env.local     # set WXT_SUPABASE_URL, WXT_SUPABASE_PUBLISHABLE_KEY, WXT_API_BASE_URL
npm run build                  # writes the unpacked extension to build/chrome-mv3/
```

In Chrome 148 or newer, open `chrome://extensions`, turn on Developer mode, choose "Load
unpacked" and select `extension/build/chrome-mv3`. Sign in from the side panel with the
same account. See `extension/README.md` for what it does, what it sends to the API, and
the store-build rules; it is not published to the Chrome Web Store.

### Optional: Telegram, Discord, Gmail, the LaTeX service

These are off until configured. The Telegram bridge needs `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_WEBHOOK_SECRET` and a public HTTPS URL for the webhook, which
`scripts/set_telegram_webhook.py` registers; Gmail drafts need a Google OAuth client, and
the Google consent includes read access to Gmail (see "What works today"); PDF output
needs `latex-service/` running; Discord has its own walk-through below. `.env.example` explains each one. Setting `WEB_APP_URL` to
the web app's public https address lets the bot's `/privacy` and `/learn` link to its pages.

### Discord

The Discord bridge is optional and off until you set its values. It works over Discord's
HTTP interactions, not the Gateway: Discord sends a signed request to
`https://<your-public-host>/discord/interactions` when someone runs a slash command or
taps a button in a direct message with the app, the API answers within Discord's three
seconds (a "thinking..." placeholder, or a silent acknowledgement for a button) and does the
work in the background, then edits that placeholder or sends follow-up messages through the
interaction's own 15-minute webhook.

**What people can do.** Everything is a slash command, because **text typed in a direct
message with the app is never delivered to an app that uses HTTP interactions**: it needs the
Gateway, which this server does not open. So a person who types `list` in the chat gets no
answer; they run `/list`.

| Command | What it does |
| --- | --- |
| `/link code` | Links this Discord account to a web account with the one-time code from the Integrations page |
| `/unlink` | Detaches it again |
| `/list`, `/apply number` | Numbers the tracked applications; generates a resume for one of them |
| `/job title company description [location] [url]` | Tracks a job (the description is up to 6,000 characters, Discord's limit for a command option) |
| `/import file` | Imports a resume from a filled-in JSON file (`/setup` gives the template) |
| `/setup`, `/resume` | The resume template; the resume on file |
| `/privacy`, `/learn` | What is stored and who handles it; the getting-started checklist |

A generation is one message edited as the work advances, the PDF arrives as a follow-up,
and the final message carries the "Mark as applied" button; nothing is ever submitted for
the person. Commands are registered for the direct message with the app only, and anything
that arrives from a server channel or a group chat is refused.

**Setting it up.**

1. In the [Discord developer portal](https://discord.com/developers/applications) create an
   application. On *General Information* copy the **Application ID** into
   `DISCORD_APPLICATION_ID` and the **Public Key** into `DISCORD_PUBLIC_KEY`. Both together
   turn the endpoint on; one without the other stops the API from starting.
2. On *Installation*, enable **both** installation contexts. *User Install* lets a person
   add the app to their own account and use its commands in their direct message with it
   (the commands are registered for that direct message, which Discord calls the `BOT_DM`
   context). *Guild Install* (scopes `applications.commands` and `bot`) gives the app a bot
   user in a server. Which people can reach the direct message through which install is
   Discord's rule, and its documentation does not say whether an app that was only installed
   to a person's account can message them unprompted, so what a push does for such a person
   is decided by Discord when it is tried (see step 3). The web app's Integrations page points
   people at the app's install link. With `DISCORD_INSTALL_URL` empty that is built from
   `DISCORD_APPLICATION_ID` as Discord's own link
   (`https://discord.com/oauth2/authorize?client_id=<application id>`), which works once
   *Installation* -> *Install Link* is set to **Discord Provided Link**. Set
   `DISCORD_INSTALL_URL` only for a Custom URL or a vanity link; it may carry a query string,
   and a value that is not an https address is ignored with a warning.
3. Optional: on *Bot*, reset the token and put it in `DISCORD_BOT_TOKEN`. It is what lets the
   server open a direct message and send into it, which pushes (the strong-match alert) and
   the delivery of a reply after a command's 15-minute token has expired both need. Whether a
   person can be messaged this way is Discord's decision, made from their own settings:
   Discord's support pages list what stops one account messaging another (no server in
   common, direct messages turned off for the shared server, accepting messages from
   friends only, a block). When Discord refuses (error 50007, "Cannot send messages to this
   user"), the push is skipped on Discord, logged with that code, and the person's other
   channels still get it. The token is a secret; keep it in the environment, never in git.
4. Register the commands, from your shell (the script does not read `.env` and never prints
   the token): `python scripts/register_discord_commands.py` is a dry run that shows what
   would change, `--apply` does it. It **replaces** the application's global commands; it
   refuses to delete ones this repository does not define unless you pass
   `--remove-others`, and `--integration-types guild|user|both` narrows who may install.
5. Put `https://<your-public-host>/discord/interactions` in *General Information* ->
   *Interactions Endpoint URL* and save. Discord sends a ping, and then purposely sends
   requests with invalid signatures, and removes the URL if the endpoint does not answer
   them correctly (the endpoint answers the ping and refuses every unverified request with
   401). An application has one endpoint: use a separate application for dev and prod.

Every request is verified (an Ed25519 signature over the timestamp and the raw body, with the
public key) before it is parsed, a timestamp more than five minutes from now is refused, and
the interaction's id is claimed in the database, so a repeat of the same interaction is
dropped instead of processed again. That is a safety net, not a gate: if the claim cannot be
made (the database is unreachable, does not answer within about a second and a half, or the
migration has not been applied) the interaction is processed anyway, and a replay of it inside
the five minutes a timestamp is accepted for can run twice. Interaction tokens and the bot token are never logged. The limits that matter in
practice: a command option holds at most 6,000 characters (so a resume is imported as a file,
not pasted); a message over Discord's 2,000-character limit is sent as several messages; and
an app that was only installed to a person's account may send at most five follow-ups to one
interaction.

## Run the API with Docker

```bash
cp .env.example .env   # then fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
docker build -t between-jobs-api .
docker run -d --rm --name between-jobs-api -p 8000:8000 --env-file .env between-jobs-api
curl localhost:8000/health     # give it a few seconds to start; `docker logs between-jobs-api` if it does not answer
docker stop between-jobs-api   # stops it and removes the container
```

`--env-file` keeps the service-role key out of your shell history and out of `ps`. Docker
reads it as plain `KEY=value` lines (no quotes, no `export`), and the image never
contains it: the build copies only the package.

- **Apply the database migrations first** (`supabase db push`). Until they are there the
  background workers that need them report it and `/health` answers 503, on purpose.
- Inside the container `127.0.0.1` is the container itself, so it cannot reach a local
  Supabase stack at the address `supabase status` prints. Point `SUPABASE_URL` at the
  host instead (on Docker Desktop, `http://host.docker.internal:54321`), or use a hosted
  project.
- Configuration is environment variables; `.env.example` lists them all. Telegram and
  Discord are optional -- leave `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` (and
  `DISCORD_APPLICATION_ID` and `DISCORD_PUBLIC_KEY`) unset to run web-only.
- **One container, one process.** The API runs its own background workers, so do not
  pass `--workers`. If a second copy ever starts (an overlapping deploy, a stray
  replica), the workers that would otherwise do the same work twice stand by instead.
- The container exits cleanly on SIGTERM. Give your platform a grace period longer than
  30 seconds before it escalates to SIGKILL (on Railway, set
  `RAILWAY_DEPLOYMENT_DRAINING_SECONDS`; its default is 0): the API waits up to 20 seconds
  for open requests, then up to 5 more for Discord commands still being handled, then up to 5
  more for a Telegram- or Discord-started resume generation to finish before it cancels one. A generation cut off by a restart is not resumed: the person
  sends the request again.

## Operations

Nothing here reports to anyone unless you configure it (`.env.example` lists every setting).
Set `SENTRY_DSN` and the API sends Sentry its unhandled exceptions, API errors answered with a
5xx, failed background-worker ticks and chat-started generations that crashed, with request
bodies, query strings, cookies, all but a few ordinary headers, local variables, user email and
address and the messages of database, validation, provider and API errors left out, and known
secret shapes (JWTs, bearer tokens, API keys, bot tokens, email addresses) removed from the text
that remains. That last step is a safety net, not a guarantee: read `src/between_jobs/api/sentry_scrub.py` before pointing it at real traffic. Set a
`HEALTHCHECKS_URL_*` setting and the matching worker sends that Healthchecks.io check an empty
GET after each successful tick, so you hear when it goes quiet; it carries no user data. For an
uptime monitor use `/health`, which answers 503 when a worker has died or stalled.

### Telegram webhook probe

Telegram stops delivering messages silently when the webhook's address is wrong, unreachable or
answers errors, and nothing in this service's own logs shows it, because nothing arrives. On a
server with a bot, about once a day (and again just after the API starts) the API asks Telegram
itself, with `getWebhookInfo`, and decides: **problem** if the bot has no webhook, the webhook's
address does not end in `/telegram/webhook` (the route this API serves, so Telegram is
delivering to something else), the bot is subscribed to a list of update types that leaves out
`message` or `callback_query` (a missing or empty list is Telegram's default and is fine), more
than 10 updates are waiting, or Telegram reports a delivery error or a synchronization error from
the last 25 hours (Telegram's documentation does not say when it stops reporting an old error, so
an older one is ignored); **ok** if none of that holds; **unknown** if the answer was missing,
malformed or never came. It runs inside the hiring-signal cache purge worker, so that worker must
be on, and it is off on a server with no bot.

The reason codes in `/health` and the log, for a **problem**:

- `no_webhook_url`: the bot has no webhook set.
- `webhook_path_unexpected`: the webhook's address does not end in `/telegram/webhook`. A proxy
  that strips a path prefix does not trip this; a webhook pointed at another service's route does.
- `update_types_excluded`: the bot's subscription leaves out `message` or `callback_query`, which
  are the only update types the webhook handler reads.
- `pending_updates_high`: more than 10 updates are waiting for delivery.
- `recent_delivery_error`: Telegram failed to deliver to the webhook within the last 25 hours.
- `recent_sync_error`: Telegram reports an error from the last 25 hours while synchronizing
  updates with its own datacenters. Its documentation defines it only that way, which is not a
  failure to reach your webhook, and does not say whether it clears. It counts as a problem
  because updates may be delayed. There is nothing to fix on your side: it ages out after 25
  hours, and the check stays down until the next healthy probe.

An **unknown** names what was missing or malformed (`url_invalid`, `pending_update_count_invalid`,
`last_error_date_invalid`, `last_synchronization_error_date_invalid`, `allowed_updates_invalid`)
or how asking Telegram failed (`telegram_timeout`, `telegram_unreachable`, `telegram_refused`,
`telegram_answer_malformed`, `probe_timed_out`, `probe_failed`).

A problem is one WARNING in the log, with short reason codes and no webhook address or token. It
also shows in `/health` under `telegram_webhook` (`status`, `last_checked_at`, `reasons`), which
never changes `/health`'s status code, so a broken webhook cannot stop a deploy. Nothing goes to
Sentry. To get alerted, set `HEALTHCHECKS_URL_TELEGRAM_WEBHOOK` to a Healthchecks check's ping
URL (period 1 day, grace 6 hours): the probe pings it when ok, pings its `/fail` address on a
problem, and sends nothing when unknown, so the missing ping is what alerts you when Telegram
cannot be reached or the bot token is wrong. Create that check only on a server with a bot and
with the purge worker on.

A new Healthchecks check stays in its "new" state, which never alerts, until it receives its
first ping, so a missing ping only alerts once the check has been pinged at least once. After
creating the check and setting the URL, restart the API (the probe runs right after it starts),
or send one manual GET to the ping URL, and confirm in Healthchecks that the check turns green
(up) before you rely on it. If it stays "new" after the first probe, the probe is not getting an
answer from Telegram: read the log WARNING and `/health` `telegram_webhook`.

Three limits. Telegram records a delivery error only when it tries to deliver an update, so a dead
webhook address on a bot nobody has messaged since shows nothing until the first message. The
probe does not know this server's public address, so a webhook re-pointed at another live service
on this API's own path (for example a dev copy of the API on another host) still reads ok; check
the host Telegram has with `python scripts/set_telegram_webhook.py --info`. And the probe runs in
every copy of the API (see "One container, one process"), so a stray second replica asks
Telegram, and pings the check, a second time a day; the answer is the same.

To see it work, use a separate dev bot and a dev API with its own check (never the production
bot, which has one webhook): point the dev bot at a host that is not your API with
`python scripts/set_telegram_webhook.py --url https://<another site you control> --replace-existing`,
send the bot a message so Telegram tries to deliver it and records an error, then restart the dev
API. The log shows the WARNING (`recent_delivery_error`), `/health` shows `problem`, and the check
goes down. Put the real address back with the same script.

## Self-hosting the job registry

The job registry is the list of company job boards that the API's background poller
reads, plus every posting it has seen; Discover searches it alongside the live-search
providers. **The hosted service's registry data is not part of this repository.** The
migrations create the tables empty, so a self-hosted install starts with an empty
registry and relies on the live-search providers (RemoteOK and Arbeitnow need no key;
the others use the keys you save on the Integrations page) until you load your own.

To load your own, add rows to `job_registry_companies`: a `name`, an `ats_type` (one of
greenhouse, lever, ashby, workday, smartrecruiters, workable, recruitee, amazon, apple,
deshaw, oracle, eightfold, avature, successfactors, google), the board's `slug` and, for the
ATS types that need one, an `api_base`. That is data, not schema, so a plain SQL insert or
the Supabase table editor is fine. What `slug` and `api_base` mean depends on the ATS
(values below are placeholders):

| `ats_type` | `slug` | `api_base` |
| --- | --- | --- |
| greenhouse, lever, ashby | The board token in the public board URL (`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`) | Leave empty |
| smartrecruiters, workable | The company identifier in the public board URL (`jobs.smartrecruiters.com/<slug>`, `apply.workable.com/<slug>`) | Leave empty |
| recruitee | The subdomain in `<slug>.recruitee.com` | Leave empty |
| workday | The career-site name, the first path segment of the board URL (`External` in `acme.wd5.myworkdayjobs.com/External`) | Required: `tenant.wdN`, for example `acme.wd5`. A bare tenant name fails |
| oracle | The candidate-experience site number, the segment after `/sites/` in the board URL (for example `CX_1001`) | Required: the Oracle Fusion pod host, no scheme (for example `abcd.fa.us2.oraclecloud.com`) |
| eightfold | The `domain` value the tenant's search requests carry, usually the company's own domain (for example `acme.com`) | Required: the host that serves the tenant, no scheme (for example `acme.eightfold.ai`) |
| successfactors | Only a label for the board (it is part of the board's key, not of the request) | Required: the career-site host, no scheme |
| avature | The tenant subdomain in `<slug>.avature.net`, or the full host when the board runs on its own domain (a `slug` containing a dot) | Optional: `portal` or `portal/listingPage`; the default is `careers/SearchJobs` |
| amazon, apple, deshaw, google | Any label: each reads one fixed board and ignores both columns | Leave empty |

`(ats_type, slug, api_base)` must be unique. A row with a missing or malformed `api_base`
does not fail on insert: it shows up as a failed poll, with `consecutive_failures` going
up. The poller starts with up to eight never-polled boards on each 15-minute tick. There
is no importer for a raw company list in `scripts/`; what is there:

- `scripts/copy_registry_sample.py` copies a sample of a registry from one Supabase
  project to another, so it needs a project that already has one.
- `scripts/import_geo_gazetteer.py` and `scripts/import_company_tiers.py` each load an
  optional reference table (a city gazetteer for location matching, and company tiers for
  the company-health score) from a JSON file you point them at with `--source`. The data
  files are not in this repository, so you build them: each script's `--help` gives the
  exact shape, the gazetteer can come from GeoNames' `cities15000` dump (CC BY 4.0, so
  keep the attribution), and the tier weights are a list you curate. `--dry-run` counts
  the rows (and, for the tiers, checks the keys) without writing. Discover still works
  without either table: the location filter then does nothing, and the company-health
  score is marked not applicable.
- `scripts/sign_and_publish_ats_field_map.py` signs the extension's per-ATS field maps;
  its docstring explains the key handling. The signed maps are not in this repository
  either, so the extension runs on its open-source generic defaults.
- `scripts/set_telegram_webhook.py` points a Telegram bot at the API's webhook; the
  `TELEGRAM_WEBHOOK_SECRET` comment in `.env.example` shows how to run it.
- `scripts/register_discord_commands.py` registers the Discord app's slash commands (a dry
  run unless you pass `--apply`); see "Discord" under "Set it up".
- `scripts/delete_account.py` and `scripts/check_migration_drift.py` are operator tools.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and checks, and
[CLAUDE.md](CLAUDE.md) for the engineering conventions.

## License

AGPL-3.0 -- see [LICENSE](LICENSE).
