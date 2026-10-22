// Which of the providers a person can save a key for has had a successful run against the real
// service, and the rows of the Settings page that show it.
//
// Serper, Brave, JSearch, Adzuna, USAJobs, Apollo, Hunter and Exa are built and tested against
// stand-in responses or the provider's own documentation, and nothing in this repository records a
// successful run of any of them against the real service with a real key. (Apollo's key check has
// run live, but only with a made-up key or none.) The Settings page puts that on the cards it has
// for them: Serper, Brave, Apollo, Hunter and Exa. JSearch, Adzuna and USAJobs accept a key through
// the API but the page has no card for them yet, so their entries here are shown nowhere until a card
// exists; a card for one of them will carry its label, and JSearch's caveat, without a change here.
// Nothing is hidden or switched off by any of this: a provider is only asked when the person has saved
// a key for it (`search_jobs` in src/between_jobs/api/search_providers.py for job search,
// src/between_jobs/api/contact_research_routes.py for Apollo, Hunter and Exa), so a provider nobody set
// up is never used to begin with.
//
// The model providers other than OpenRouter (OpenAI, Anthropic, Google, xAI, DeepSeek, Qwen Cloud and
// the allowlisted hosts of the "custom" provider) are in the same position, and the registry that
// describes them says so itself: "A real key was not available, so no provider's behaviour here has
// been seen against the live service" (src/between_jobs/api/provider_registry.py).
//
// THE TABLE IS A DECISION, NOT A GUESS. Every provider the server accepts a key for
// (`_SUPPORTED_CREDENTIALS` in src/between_jobs/api/credentials_routes.py, and the model providers of
// `PROVIDERS` in provider_registry.py) must have an entry, and the table may name no provider the
// server does not know: providerStatus.test.tsx reads both files and fails otherwise, so adding a
// provider forces someone to say, here, whether it has run for real. There are two answers:
//   - "not_verified_live": no successful live run is recorded; the page says so.
//   - "no_claim": this page says nothing about it either way (OpenRouter, You.com and Firecrawl).
//     This is NOT a statement that it has been verified, and it does not say it has not: an unknown
//     is not turned into a "verified". For You.com and Firecrawl the repository does record
//     measurements against the real APIs (the notes in src/between_jobs/api/hiring_signal_search.py
//     and tests/golden/hiring_signals/provider_responses/README.md); nothing is claimed here for
//     OpenRouter.
// Move a provider to a third state, with a label of its own, only when someone has watched it
// work for real.
//
// Pure module: no React, no network.

import { PROVIDER_NAMES, providerDisplayName } from "./providerNames";
import { SEARCH_KEY_PROVIDERS } from "./setupReads";

// What a key unlocks, as the Settings page groups its optional extras. A provider is drawn once, under
// the first thing it is used for; its note names every use (a test reads the server's code for them).
//
//   jobs    Discover's live job search: `search_jobs` in search_providers.py asks every one of You.com,
//           Firecrawl, Brave and Serper that has a key.
//   people  contact enrichment and lookup: Apollo and Hunter (contact_research_routes.py) and Exa
//           (contact_enrichment.py). They look a person up and run no job search.
//
// HIRING POSTS IS NOT A GROUP OF KEYS. Hiring signals searches with the same four providers as live job
// search (hiring_signal_search.py), so no key is for hiring posts alone: it is a line under the groups,
// shown only to a person Hiring signals is enabled for, that says which keys it uses.
export type SearchGroupId = "jobs" | "people";

export const SEARCH_GROUPS: readonly { id: SearchGroupId; title: string }[] = [
  { id: "jobs", title: "Live job search" },
  { id: "people", title: "Find people" },
];

export const HIRING_POSTS_TITLE = "Hiring posts";

// What the "Hiring posts" line says, for a person Hiring signals is enabled for: which keys it runs on.
// The names come from the same list Discover and Hiring signals count a search key by.
export function hiringPostsLine(): string {
  const names = SEARCH_KEY_PROVIDERS.map(providerDisplayName);
  return `Hiring signals uses the live job search keys: any one of ${names.slice(0, -1).join(", ")} or ${names[names.length - 1]} is enough.`;
}

export interface SearchProviderCardSpec {
  // The card's heading, as the person sees it.
  title: string;
  // Which group of the optional extras the row sits in.
  group: SearchGroupId;
  // The server's name for the provider (credentials_routes.py): the key it is saved under.
  provider: string;
  placeholder: string;
  // The provider's own page for creating a key (https). The server has no catalogue for search
  // providers, so these are kept here with the rest of what the page says about each.
  getKeyUrl: string;
  // What the check on save really was, in the words the trail line uses ("a free credit-usage read"):
  // the same fact the note states about cost, said as a basis. Code made the check, not a model.
  checkNote: string;
  // What the card says the key is for. Never names a feature the person may not have (see
  // `hiringNote`).
  note: string;
  // The same note for a person who has Hiring signals, which also uses this provider. It is shown
  // only to them: for anyone the feature is not enabled for, the server answers every one of its
  // routes as if it did not exist, so a card that told them their key finds hiring posts would be
  // false, and would advertise a feature that does not exist for them.
  hiringNote?: string;
}

// What a card's note says for this person: the one that mentions Hiring signals only when the
// feature is on for them (`useHiringSignalsEnabled`, which is false while that is being asked and
// when asking failed).
export function cardNote(card: SearchProviderCardSpec, hiringSignalsEnabled: boolean): string {
  return hiringSignalsEnabled && card.hiringNote !== undefined ? card.hiringNote : card.note;
}

const YOU_VALIDATION = "Validating costs a small real charge (~$0.005) -- a live search call, since You.com has no free key-check endpoint.";
const FIRECRAWL_VALIDATION = "Validated against your account's credit usage -- doesn't spend a search/scrape credit.";
const BRAVE_VALIDATION = "Validating runs one tiny real search call -- Brave has no free key-check endpoint.";
const SERPER_VALIDATION = "Validating runs one 1-credit search call -- Serper has no free key-check endpoint.";

// The search-provider rows of the Settings page, in the order the page shows them (after the model
// provider section). The page draws one card per entry, so a provider with a card cannot be
// missing from the status table below without the test noticing.
export const SEARCH_PROVIDER_CARDS: readonly SearchProviderCardSpec[] = [
  {
    title: PROVIDER_NAMES.you_com,
    group: "jobs",
    provider: "you_com",
    placeholder: "your-you-com-key",
    getKeyUrl: "https://you.com/platform/api-keys",
    checkNote: "one small search call",
    note: `Used for job search on Discover, and to research companies and contacts. ${YOU_VALIDATION}`,
    hiringNote: `Used for job search on Discover, to find hiring posts on an application, and to research companies and contacts. ${YOU_VALIDATION}`,
  },
  {
    title: PROVIDER_NAMES.firecrawl,
    group: "jobs",
    provider: "firecrawl",
    placeholder: "fc-...",
    getKeyUrl: "https://www.firecrawl.dev/app/api-keys",
    checkNote: "a free credit-usage read",
    note: `Used for job search on Discover, to fetch a job posting from a link, and to research companies and contacts. ${FIRECRAWL_VALIDATION}`,
    hiringNote: `Used for job search on Discover, to fetch a job posting from a link, to find hiring posts on an application, and to research companies and contacts. ${FIRECRAWL_VALIDATION}`,
  },
  {
    title: PROVIDER_NAMES.brave,
    group: "jobs",
    provider: "brave",
    placeholder: "your-brave-key",
    getKeyUrl: "https://api-dashboard.search.brave.com/documentation/guides/authentication",
    checkNote: "one tiny search call",
    note: `Used for job search on Discover. ${BRAVE_VALIDATION}`,
    hiringNote: `Used for job search on Discover and to find hiring posts on an application. ${BRAVE_VALIDATION}`,
  },
  {
    title: PROVIDER_NAMES.serper,
    group: "jobs",
    provider: "serper",
    placeholder: "your-serper-key",
    getKeyUrl: "https://serper.dev/api-keys",
    checkNote: "one 1-credit search call",
    note: `Used for job search on Discover. ${SERPER_VALIDATION}`,
    hiringNote: `Used for job search on Discover and, as a last resort, to find hiring posts on an application (it returns Google results). ${SERPER_VALIDATION}`,
  },
  {
    title: PROVIDER_NAMES.apollo,
    group: "people",
    provider: "apollo",
    placeholder: "your-apollo-key",
    getKeyUrl: "https://docs.apollo.io/docs/create-api-key",
    checkNote: "a free health check",
    note: "Used only for contact enrichment, one already-selected person at a time -- never a bulk search. Validated against Apollo's free health-check endpoint, at no cost.",
  },
  {
    title: PROVIDER_NAMES.hunter,
    group: "people",
    provider: "hunter",
    placeholder: "your-hunter-key",
    getKeyUrl: "https://hunter.io/api-keys",
    checkNote: "a free account read",
    note: "A second contact-enrichment provider, tried automatically alongside Apollo for one already-selected person -- never a bulk search. Validated against Hunter's free account endpoint, at no cost.",
  },
  {
    title: PROVIDER_NAMES.exa,
    group: "people",
    provider: "exa",
    placeholder: "your-exa-key",
    getKeyUrl: "https://dashboard.exa.ai/api-keys",
    checkNote: "one small search call",
    note: "Finds a LinkedIn profile URL for one already-selected contact who doesn't already have one -- never an email. Validating costs a small real charge -- a live search call, since Exa has no free key-check endpoint.",
  },
];

export type LiveStatus = "not_verified_live" | "no_claim";

export interface ProviderStatusEntry {
  status: LiveStatus;
  // A caveat about this provider in particular, shown under the general notice.
  help?: string;
}

export const NOT_VERIFIED_LIVE_LABEL = "Not yet verified live";

// What the page says under the label, the same for every provider that carries it. The last sentence
// is true of all eight: each is only asked behind the person's own saved key (a test reads the
// server's code for it).
export const NOT_VERIFIED_LIVE_DETAIL =
  "This integration has not had a successful run against the real service yet, so it may fail or return nothing. It is only asked when you have saved a key here.";

// Shown only on a JSearch card, which the page does not have yet (see the top of this file).
const JSEARCH_HELP =
  "JSearch's remote-job filter parameter name is also unconfirmed (remote_jobs_only or work_from_home), so this integration sends no remote filter and relies on the remote flag in each result instead.";

// What the code records of Apollo: the health check was run against the real service with a made-up
// key and with none (credentials_routes.py, `_validate_apollo_key`), and it is the answer for a valid
// key that nothing here records.
const APOLLO_HELP =
  "Its key check has been run against the real service only with a made-up key or none; no run with a valid key is recorded.";

// One entry per provider the server accepts a key for.
export const PROVIDER_STATUS: Readonly<Record<string, ProviderStatusEntry>> = {
  openrouter: { status: "no_claim" },
  openai: { status: "not_verified_live" },
  anthropic: { status: "not_verified_live" },
  gemini: { status: "not_verified_live" },
  xai: { status: "not_verified_live" },
  deepseek: { status: "not_verified_live" },
  qwen: { status: "not_verified_live" },
  custom: { status: "not_verified_live" },
  you_com: { status: "no_claim" },
  firecrawl: { status: "no_claim" },
  apollo: { status: "not_verified_live", help: APOLLO_HELP },
  hunter: { status: "not_verified_live" },
  exa: { status: "not_verified_live" },
  serper: { status: "not_verified_live" },
  brave: { status: "not_verified_live" },
  jsearch: { status: "not_verified_live", help: JSEARCH_HELP },
  adzuna: { status: "not_verified_live" },
  usajobs: { status: "not_verified_live" },
};

export interface ProviderNotice {
  label: string;
  detail: string;
  help: string | null;
}

// What a provider's card shows about live verification, or null when it makes no claim. A name
// that is not in the table is "no claim" as well, never "verified": a provider the table does not
// know is exactly the case the test refuses to let reach a release.
export function providerNotice(provider: string): ProviderNotice | null {
  const entry = Object.hasOwn(PROVIDER_STATUS, provider) ? PROVIDER_STATUS[provider] : undefined;
  if (entry === undefined || entry.status !== "not_verified_live") return null;
  return { label: NOT_VERIFIED_LIVE_LABEL, detail: NOT_VERIFIED_LIVE_DETAIL, help: entry.help ?? null };
}

// ── what a model provider costs the person ────────────────────────────────

export const OPENROUTER_PRICING_URL = "https://openrouter.ai/pricing";

// Who bills the person, when that is not the provider's own name in the catalogue.
const BILLED_BY: Readonly<Record<string, string>> = {
  gemini: "Google",
  qwen: "Alibaba Cloud",
};

export interface ProviderCost {
  text: string;
  /** A page that states the price, when the provider has one the page can point at. */
  link: { label: string; href: string } | null;
}

// Said plainly and without a number: this app does not know what a provider charges, and a figure
// copied into the page would go stale without anyone noticing. OpenRouter is the one that adds a fee
// of its own (when credits are bought), so it says so and points at the page that states it. Everyone
// else bills on the person's own account with that provider, and the app takes nothing.
export function providerCost(providerId: string, providerName: string): ProviderCost {
  if (providerId === "openrouter") {
    return {
      text: "OpenRouter adds a fee when you buy credits, on top of the price of the models you use.",
      link: { label: "See OpenRouter's pricing", href: OPENROUTER_PRICING_URL },
    };
  }
  if (providerId === "custom") {
    return { text: "Billed by whichever provider's address you choose, on your own account with them.", link: null };
  }
  return { text: `Billed by ${BILLED_BY[providerId] ?? providerName} on your own account.`, link: null };
}
