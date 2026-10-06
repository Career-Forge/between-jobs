// The landing page's words, as data, so a test can check every claim against what the product
// does today. Plain on purpose: no superlatives, no promise of anything that is not built.
// Every sentence was checked against the code; change one only after checking it again.

import { SOURCE_LICENSE } from "./site";

export const LANDING = {
  headline: "A workbench for your job search",
  lead: "Between Jobs helps you find roles, keep track of your applications, prepare tailored resumes and cover letters, and practice for interviews, all in one place.",
  features: [
    {
      title: "Find roles",
      text: "Search public job boards and the search providers you connect, and see how each listing fits your profile.",
    },
    {
      title: "Track applications",
      text: "Keep every application on a board, from saved through to offer, rejected or withdrawn.",
    },
    {
      title: "Prepare documents",
      text: "Draft a resume and a cover letter for a posting, based on your own profile, and review them before you use them.",
    },
    {
      title: "Practice interviews",
      text: "Practice questions for an application you are tracking, with written feedback on each of your answers.",
    },
  ],
  promise: {
    title: "You always submit",
    text: "Between Jobs drafts and prepares. It never submits an application for you and never sends an email from your mailbox. The browser extension fills in a form and stops. The last step is always yours.",
  },
  // What the optional Gmail connection does, in full: it is more than a way to write drafts, and
  // a sentence that said only that would be untrue by omission. Its own paragraph, with a link
  // to the Privacy Policy that has the details (rendered by pages/Landing.tsx).
  gmail: {
    text: "The optional Gmail connection creates drafts for you to send. It also reads the thread of each draft it created, to look for replies: the text of the newest reply goes to your AI provider to be classified, and a confident result can move that application to a new stage on your board.",
    policyLead: "The ",
    policyLinkText: "Privacy Policy",
    policyTail: " has the details.",
  },
  keys: {
    title: "Bring your own keys",
    text: "Between Jobs has no AI provider of its own. You save your own OpenRouter key, and optionally your own search and research keys. The keys are stored encrypted, the providers bill you directly, and you can remove a key whenever you like.",
  },
  source: {
    title: "Open source",
    text: `The web app, the API and the browser extension are open source under the ${SOURCE_LICENSE} license. The service that generates resumes and cover letters is separate, and it is not in the repository yet.`,
    linkText: "View the source on GitHub",
  },
  early: {
    title: "Early days",
    lead: "Between Jobs is an early beta, so expect rough edges. Questions and bug reports are welcome at ",
  },
} as const;
