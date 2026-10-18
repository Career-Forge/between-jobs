// The landing page's words, as data, so a test can check every claim against what the product
// does today. Plain on purpose: no superlatives, no promise of anything that is not built, no
// number or statistic. Every sentence was checked against the code; change one only after
// checking it again.

import { SOURCE_LICENSE } from "./site";

// The model providers a key can be connected for: the API's provider registry, the same list the
// Settings page draws its chips from, without its "other OpenAI-compatible" slot. The Privacy Policy
// names every one (content/legal.ts); content/legal.test.ts checks that the three agree.
export const LANDING_PROVIDERS = ["OpenRouter", "OpenAI", "Anthropic", "Gemini", "xAI", "DeepSeek", "Qwen Cloud"] as const;

// The in-page sections the header links to, in page order.
export const LANDING_SECTION_IDS = {
  circuit: "circuit",
  how: "how",
  submit: "submit",
  source: "source",
  early: "early",
} as const;

export const LANDING_SECTION_LINKS = [
  { id: LANDING_SECTION_IDS.circuit, label: "Product" },
  { id: LANDING_SECTION_IDS.how, label: "How it works" },
  { id: LANDING_SECTION_IDS.submit, label: "You always submit" },
  { id: LANDING_SECTION_IDS.source, label: "Open source" },
] as const;

export const LANDING = {
  headlineLines: ["One career.", "Every surface.", "Nothing forgotten."],
  lead: "Between Jobs helps you find roles, keep track of your applications, prepare tailored resumes and cover letters, and practice for interviews, all in one place.",
  motto: "Draft, don't send. Fill, don't submit.",
  secondaryCta: "See how it works",

  // The hero's worked example: one flagged claim in the claim check, with the trail that says who
  // acted. The sentence is invented for this page and says so.
  trailExample: {
    eyebrow: "A sample flagged claim",
    chip: "Unverifiable",
    claim: "Led the move of the evaluation harness to async workers.",
    reason: "None of the achievements picked for this draft mention async workers.",
    sampleNote: "A sample sentence, written for this page. Yours shows your own work.",
  },

  circuit: {
    title: "Follow one role from posting to offer",
    sampleLabel: "Sample role: Applied AI Engineer at Northstar Labs, a fictional company. Pick a stage.",
    sampleRole: "Applied AI Engineer",
    sampleCompany: "Northstar Labs",
    stagesLabel: "Stages of the sample role",
    demoLabel: "A sample next step",
    boardLabel: "Then it lives on your board",
    boardText:
      "Every role you track sits on a board, from Saved through Offer, Rejected or Withdrawn. You move it between them.",
  },

  dossier: {
    eyebrow: "A workspace for what comes next",
    title: "One role. Every piece of work together.",
    text: "A role is a dossier: the posting, your evidence, the resume and cover letter drafted from it, the people to write to, the practice questions. Open it and everything is there.",
    sampleChip: "Sample dossier",
    sampleNote: "Drawn for this page. Your own dossiers show your own roles.",
  },

  chapters: [
    {
      title: "Discover",
      text: "Search public job boards and the search providers you connect, and, with your own model key, see how the top listings fit your profile.",
    },
    {
      title: "Prepare",
      text: "Draft a resume and a cover letter for a posting, based on your own profile, and review them before you use them.",
    },
    {
      title: "Keep moving",
      text: "Track every application on a board, from saved through to offer, and practice interview questions with written feedback on each of your answers.",
    },
  ],

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
    availability: "Gmail drafting is currently invite-only while Google reviews the app.",
  },
  extension: {
    title: "Fills, never submits",
    text: "The browser extension fills a form page from your own profile when you press Fill, then stops. It never clicks Next and never clicks Submit. It works on Greenhouse, Lever and Ashby application pages.",
    availability: "The extension is for early testers and is not in the Chrome Web Store yet.",
  },
  keys: {
    title: "Bring your own keys",
    text: "Between Jobs has no AI provider of its own. You save your own key, and optionally your own search and research keys. The keys are stored encrypted, the providers bill you directly, and you can remove a key whenever you like.",
    connect: "Connect a key from the provider you already use.",
    providersLabel: "Model providers",
  },

  source: {
    title: "Open source",
    text: `The web app, the API and the browser extension are open source under the ${SOURCE_LICENSE} license. The service that generates resumes and cover letters is separate, and it is not in the repository yet.`,
    linkText: "View the source on GitHub",
    license: SOURCE_LICENSE,
  },

  early: {
    title: "Early days",
    lead: "Between Jobs is an early beta, so expect rough edges. Questions and bug reports are welcome at ",
    facts: [
      "Discover, the application board and interview practice are live.",
      "You bring your own AI key. Nothing is included.",
      "Gmail drafting is optional, and invite-only for now.",
      "The browser extension is for early testers and is not in the Chrome Web Store yet.",
    ],
    createAccount: "Create account",
    signIn: "Sign in",
  },
} as const;
