// The landing page's words, as data, so a test can check every claim against what the product
// does today. Plain on purpose: no superlatives, no promise of anything that is not built, no
// number or statistic. Every sentence was checked against the code; change one only after
// checking it again.

import { providerNotice } from "../lib/providerStatus";
import { SOURCE_LICENSE } from "./site";

// The model providers a key can be connected for: the API's provider registry, the same list the
// Settings page draws its chips from, without its "other OpenAI-compatible" slot. The Privacy Policy
// names every one (content/legal.ts); content/legal.test.ts checks that the three agree.
const LANDING_PROVIDER_LIST = [
  { id: "openrouter", name: "OpenRouter" },
  { id: "openai", name: "OpenAI" },
  { id: "anthropic", name: "Anthropic" },
  { id: "gemini", name: "Google Gemini" },
  { id: "xai", name: "xAI" },
  { id: "deepseek", name: "DeepSeek" },
  { id: "qwen", name: "Qwen Cloud" },
] as const;

export const LANDING_PROVIDER_IDS: readonly string[] = LANDING_PROVIDER_LIST.map((provider) => provider.id);
export const LANDING_PROVIDERS: readonly string[] = LANDING_PROVIDER_LIST.map((provider) => provider.name);

function joinNames(names: readonly string[]): string {
  return names.length < 3 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

// What the Settings page says about these providers, said here too. The page marks a provider "Not
// yet verified live" (lib/providerStatus.ts) until a successful run against the real service is on
// record, and the landing page may not promise more than the page the visitor lands on next. It
// words this as "not yet checked live" because this page never says "verified" (Landing.test.tsx).
// Built from that table, so the two cannot drift: a provider that is verified there drops out here.
const NOT_VERIFIED = LANDING_PROVIDER_LIST.filter((provider) => providerNotice(provider.id) !== null).map((provider) => provider.name);
export const LANDING_PROVIDER_CAVEAT: string | null =
  NOT_VERIFIED.length === 0
    ? null
    : `In Settings, ${joinNames(NOT_VERIFIED)} ${NOT_VERIFIED.length === 1 ? "is" : "are"} flagged as not yet checked live: ${NOT_VERIFIED.length === 1 ? "it has" : "they have"} not had a successful run against the real service yet, so ${NOT_VERIFIED.length === 1 ? "it" : "they"} may fail or return nothing.`;

// The in-page sections of the home page, in page order. Hero links and the tests point at these.
export const LANDING_SECTION_IDS = {
  tour: "tour",
  submit: "submit",
  source: "source",
  early: "early",
} as const;

// The two availability lines below (Gmail, the Chrome Web Store) describe where the launch stands
// today and are not derived from code. Re-read them, and tour.ts and legal.ts, when Google
// finishes its review or the extension is listed. The Gmail one is worded as a possibility on
// purpose: the invite list is an optional server setting (GMAIL_ALLOWED_USER_IDS), unset means open.

export const LANDING = {
  headlineLines: ["One career.", "Every surface.", "Nothing forgotten."],
  lead: "Between Jobs helps you find roles, keep track of your applications, prepare tailored resumes and cover letters, and practice for interviews, all in one place.",
  motto: "Draft, don't send. Fill, don't submit.",
  secondaryCta: "Take the tour",

  // The hero's worked example: one flagged claim in the claim check, with the trail that says who
  // acted. The sentence is invented for this page and says so.
  trailExample: {
    eyebrow: "A sample flagged claim",
    chip: "Unverifiable",
    claim: "Led the move of the evaluation harness to async workers.",
    reason: "None of the achievements picked for this draft mention async workers.",
    sampleNote: "A sample sentence, written for this page. Yours shows your own work.",
  },

  promise: {
    title: "You always submit",
    text: "Between Jobs drafts and prepares. It never submits an application for you and never sends an email from your mailbox. The browser extension fills in a form and stops. The last step is always yours.",
  },
  // What the optional Gmail connection does, in full: it is more than a way to write drafts, and
  // a sentence that said only that would be untrue by omission. Its own paragraph, with a link
  // to the Privacy Policy that has the details (rendered by pages/Landing.tsx). Google's review of
  // the app asks for exactly this on the home page.
  gmail: {
    title: "Drafts, never sends",
    text: "The optional Gmail connection creates drafts for you to send. It also reads the thread of each draft it created, to look for replies: the text of the newest reply goes to your AI provider to be classified, and a confident result can move that application to a new stage on your board.",
    policyLead: "The ",
    policyLinkText: "Privacy Policy",
    policyTail: " has the details.",
    availability: "Gmail drafting may be limited to invited people while Google reviews the app.",
  },
  extension: {
    title: "Fills, never submits",
    text: "The browser extension fills a form page from your own profile when you press Fill, then stops. It never clicks Next and never clicks Submit. It is built for Greenhouse, Lever and Ashby application pages.",
    availability: "The extension is for early testers and is not in the Chrome Web Store yet. It has not been checked in a real browser against live application pages yet.",
  },
  keys: {
    title: "Bring your own keys",
    text: "Between Jobs has no AI provider of its own. You save your own key, and optionally your own search and research keys. The keys are stored encrypted, the providers bill you directly, and you can remove a key whenever you like.",
    connect: "Connect a key from the provider you already use.",
    providersLabel: "Model providers",
  },

  source: {
    title: "Open source",
    text: `The web app, the API and the browser extension are open source under the ${SOURCE_LICENSE} license. A basic resume and cover-letter writer is built in and answers when no separate engine service is set up. The larger engine service the hosted version uses is not in the repository.`,
    linkText: "View the source on GitHub",
    pageLinkText: "What is in the repository",
    license: SOURCE_LICENSE,
  },

  early: {
    title: "Early days",
    lead: "Between Jobs is an early beta, so expect rough edges. Questions and bug reports are welcome at ",
    facts: [
      "Discover, the application board and interview practice are live.",
      "You bring your own AI key. Nothing is included.",
      "Gmail drafting is optional, and may be limited to invited people for now.",
      "The browser extension is for early testers and is not in the Chrome Web Store yet.",
    ],
    createAccount: "Create account",
    signIn: "Sign in",
  },
} as const;
