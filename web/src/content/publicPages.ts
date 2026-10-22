// The words of the two explainer pages, How it works and Open source, as data so a test can scan every
// claim the way it scans the home page's (content/landing.ts). Each sentence was checked against the
// code and the repository's README when it was written: change one only after checking it again.
//
//   - How it works says, per step, what is plain code and what is a model working on the person's own
//     key, and shows one trail line of the kind the real screens draw. The imports follow
//     lib/profileTrails.ts, the fit rating lib/workspaceTrails.ts (scoreTrail), the checklist
//     api/export_checklist.py, the board and practice the application and practice screens.
//   - Open source is the README's own account of what the repository holds, how a key is handled and how
//     to run it. The self-hosting paragraphs only summarise "Set it up" there and invent no step.
//
// Plain on purpose: no superlatives, no number or statistic, nothing that is not built. Text between
// backticks is drawn as code.

import type { TrailInput } from "../lib/trail";
import { SOURCE_LICENSE } from "./site";

export interface HowStep {
  id: string;
  title: string;
  text: string;
  /** Which part of the step is plain code and which is a model on the person's own key. */
  split: string;
  /** What the example below is an example of. */
  exampleLabel: string;
  trail: TrailInput;
}

export const HOW_IT_WORKS = {
  eyebrow: "How it works",
  title: "Four steps. You make the last move in each.",
  lead: "Most people go through them in this order, and you can start anywhere. In every step, the line under a block of work says who made it, what it is based on and what nobody has checked.",
  stepsLabel: "The four steps",
  steps: [
    {
      id: "profile",
      title: "Bring your profile",
      text: "Paste your resume as JSON in the app's template, or upload a PDF or DOCX file. Everything written later comes from this profile, and one version of it is active at a time.",
      split:
        "Pasted JSON is read by plain code. A file is read by a model on your own key, so every value it found is shown for you to check, and nothing is used until you activate it.",
      exampleLabel: "Example: a profile read from an uploaded file",
      trail: {
        glyph: "model",
        actor: "Read by your model",
        basis: "from your PDF file, probabilistic",
        extra: "every field to check",
      },
    },
    {
      id: "find",
      title: "Find and fit-check",
      text: "Search the job registry and the search providers you connect, or paste a posting or a link. With your own model key, the top results get a fit rating against your profile. Track the roles worth keeping.",
      split:
        "A model reads each posting against your profile. Code turns its sub-scores into the rating, so the arithmetic is not a model's. A result past the first batch is shown as not scored.",
      exampleLabel: "Example: a fit rating",
      trail: {
        glyph: "engine",
        actor: "Rated by code from your model's sub-scores",
        basis: "the posting and your profile",
      },
    },
    {
      id: "prepare",
      title: "Prepare and review",
      text: "Generate a resume, and a cover letter if you want one, for a role you track. Then read it. A checklist shows the checks code ran next to the ones left for you, and the claim check flags sentences that nothing in your profile backs.",
      split: "A model writes the words. Code sets the shape and does the checking.",
      exampleLabel: "Example: the export checklist",
      trail: {
        glyph: "code",
        actor: "Checked by code",
        basis: "no model involved",
        extra: "some checks left for you",
      },
    },
    {
      id: "track",
      title: "Track and practice",
      text: "Every application sits on a board from Saved through Offer, Rejected or Withdrawn, and you move it between them. For a role you are interviewing for, practice questions get written feedback on each of your answers.",
      split: "The board is plain code. The feedback is a model's, on your key, and it is not a prediction of how an interview will go.",
      exampleLabel: "Example: feedback on one answer",
      trail: {
        glyph: "model",
        actor: "Scored by your model",
        basis: "on your key",
        extra: "not a prediction of an interview",
      },
    },
  ] satisfies readonly HowStep[],
  closing: {
    motto: "Draft, don't send. Fill, don't submit.",
    text: "Between Jobs never submits an application and never sends an email for you. The browser extension fills a form and stops.",
    keysLinkText: "How bring-your-own-key works",
  },
} as const;

export const OPEN_SOURCE_SECTION_IDS = {
  repository: "repository",
  keys: "bring-your-own-key",
  selfHost: "self-hosting",
} as const;

export const OPEN_SOURCE = {
  eyebrow: `Open source · ${SOURCE_LICENSE}`,
  title: "Read it, run it, change it",
  lead: `The core of Between Jobs is open source under the ${SOURCE_LICENSE} license. If you run a changed version as a service for other people, the license asks you to offer them the source of your changes.`,

  repository: {
    title: "What is in the repository",
    items: [
      { name: "The API", text: "A FastAPI service: the routes, the stores, the background workers and the model-provider clients." },
      { name: "The web app", text: "React and TypeScript, a thin client over the API. This site is part of it." },
      {
        name: "The browser extension",
        text: "A Chrome extension that fills Greenhouse, Lever and Ashby application forms from your profile and stops before Submit.",
      },
      {
        name: "A built-in resume writer",
        text: "A basic engine that writes one plain resume and one plain cover letter from your profile and a job, and checks what it wrote against the profile. It does not score them.",
      },
      { name: "The PDF service", text: "A small, separate service that compiles LaTeX to PDF." },
      { name: "The database", text: "The Supabase schema, as migration files." },
      { name: "Chat bridges", text: "Optional Telegram and Discord bridges." },
    ],
    notIncluded: "The larger resume engine service that the hosted version uses is not in the repository.",
  },

  keys: {
    title: "Bring your own key",
    text: "Between Jobs has no AI provider of its own. You save your own key, which is checked with the provider and stored encrypted, and you can remove it whenever you like. The text you process, such as your resume, job postings and your answers, goes to the provider you chose, and that provider bills you directly.",
    providersLabel: "Model providers",
    providersLead: "A key can be saved for any of these.",
    oneClick: "OpenRouter can also be connected in one click.",
  },

  selfHost: {
    title: "Self-hosting",
    paragraphs: [
      "You need Python 3.12 or newer, Node, Docker and the Supabase CLI. Everything the API stores lives in a Supabase project, either a local one from `supabase start` or a hosted one of your own, and its schema is the migration files in the repository.",
      "Then you start the API with `uvicorn` and the web app with `npm run dev`, create an account, save a model key on the Settings page and import your resume. The extension is built from its own folder and loaded unpacked in Chrome. The README has every command, the optional pieces (Telegram, Discord, Gmail and the PDF service) and what is not claimed. It is early software, so expect rough edges.",
    ],
  },

  linkText: "View the repository on GitHub",
} as const;
