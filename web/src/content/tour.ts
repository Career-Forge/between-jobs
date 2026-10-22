// The home page's product tour, as data: five tabs, each a drawing of one screen of the product built
// from one fictional role, and the sample next step that rewrites for the tab in view. Every word here
// was checked against what the product does today, the same way content/landing.ts is: Discover rates a
// result from the model's sub-scores with code and draws only what a Discover result draws (the ladder,
// the model's one line, a bar per part, the lowest part; the skills read belongs to the Tailor panel in
// an application's workspace and is not drawn); the resume checklist keeps the checks code ran apart from the ones left to the person
// (api/export_checklist.py); outreach is drafted by the person's model and, with the optional Gmail
// connection (a server may limit it to invited people), lands in their drafts; practice feedback is the
// model's; the extension fills by plain code and never clicks Next or Submit.
//
// A sample never carries a date, a number or a count, because none was read from anywhere. The tab
// numbers (01 to 05) are drawn by the component from the tab's place in the list.

import type { TrailInput } from "../lib/trail";

export type TourTabId = "discover" | "prepare" | "reach" | "practice" | "apply";

export interface TourTab {
  id: TourTabId;
  label: string;
  /** The sample next step for this tab. Never a control; the hero holds the page's one cyan fill. */
  next: { sentence: string; why: readonly string[] };
  /** The one trail line the panel ends in: who made it, what it is based on, what nobody checked. */
  trail: TrailInput;
}

export const TOUR_TABS: readonly TourTab[] = [
  {
    id: "discover",
    label: "Discover",
    next: { sentence: "Track the jobs worth a draft.", why: ["sample results", "scored against your profile"] },
    trail: {
      glyph: "engine",
      actor: "Fit rated by code from your model's sub-scores",
      basis: "the posting and your profile",
      extra: "not checked: whether a missing skill matters to the team",
    },
  },
  {
    id: "prepare",
    label: "Prepare",
    next: { sentence: "Read any flagged claims, then download.", why: ["status saved", "resume drafted", "one claim flagged"] },
    trail: {
      glyph: "model",
      actor: "Drafted by your model, checked by code",
      basis: "from your profile",
      extra: "not checked: links, fonts and repeated bullets",
    },
  },
  {
    id: "reach",
    label: "Reach out",
    next: { sentence: "Read the draft in your mailbox, then send it yourself.", why: ["draft created", "sending is yours"] },
    trail: {
      glyph: "model",
      actor: "Drafted by your model",
      basis: "on your key",
      extra: "not checked: the recipient and the tone",
    },
  },
  {
    id: "practice",
    label: "Practice",
    next: { sentence: "Read the feedback, then answer again.", why: ["written feedback is ready", "the answer names no result"] },
    trail: {
      glyph: "model",
      actor: "Scored by your model",
      basis: "on your key, from your written answer",
      extra: "not checked: whether an interviewer would agree",
    },
  },
  {
    id: "apply",
    label: "Apply",
    next: { sentence: "Fill the fields we know, then check the rest.", why: ["tracked application matches this page"] },
    trail: {
      glyph: "code",
      actor: "Filled by code",
      basis: "from your profile",
      extra: "not checked: the fields left for you",
    },
  },
];

export const TOUR = {
  title: "Take the tour",
  sampleTag: "Sample",
  sampleLabel:
    "One fictional role: Applied AI Engineer at Northstar Labs, a fictional company. Everything in the tour is a drawing, not a control.",
  tablistLabel: "Screens of the product",
  nextLabel: "A sample next step",
  role: "Applied AI Engineer",
  company: "Northstar Labs",
  place: "Remote, US",

  discover: {
    kicker: "Discover",
    posting:
      "Build and evaluate retrieval systems for internal research tools. Work in Python, write evaluation suites, ship to Kubernetes.",
    postingNote: "Sample posting text, written for this page.",
    fitKicker: "Fit check",
    fitLevel: "Good",
    fitOneLiner: "Matches Python, evals and retrieval. Kubernetes is not in your profile.",
    partsLabel: "Parts of the fit",
    // The labels are the ones a real result draws (lib/discover.ts SUB_SCORE_LABELS), and a bar carries no word
    // of its own here: a real one shows its value out of a hundred, and a sample never carries a number.
    parts: [
      { label: "Skills", filled: 4 },
      { label: "Experience", filled: 6 },
      { label: "Location", filled: 8 },
    ],
    lowest: "Lowest part: Skills",
  },

  prepare: {
    pageLabel: "A resume page, as a sample",
    name: "Your Name",
    contact: "you@example.com",
    sectionExperience: "Experience",
    job: "Evaluation harness",
    flagged: "Led",
    after: " the design of an evaluation harness for retrieval quality across three internal tools.",
    sectionSkills: "Skills",
    skills: "Python, evals, retrieval",
    flaggedSrNote: "Marked: flagged by the claim check.",
    claimsLabel: "Flagged claims",
    claim: "Led the design of an evaluation harness for retrieval quality.",
    claimReason: "None of the achievements picked for this draft say you led it.",
    checksTitle: "Yours to check",
    checks: [
      { id: "links_valid", label: "Links are valid", hint: "Review header and link fields by hand." },
      { id: "fonts_embedded", label: "Fonts are embedded", hint: "Check the PDF properties in a viewer." },
      { id: "no_duplicate_bullets", label: "No duplicate bullets", hint: "Skim the resume for repeated phrasing." },
    ],
  },

  reach: {
    kicker: "Gmail draft",
    badge: "Not sent",
    to: "A recruiter at Northstar Labs (sample)",
    subject: "Applied AI Engineer: a short introduction",
    greeting: "Hello,",
    body: "I saw the Applied AI Engineer opening. I have written evaluation suites for retrieval systems in Python and would like to hear how your team measures quality.",
    waiting: "Waiting in your drafts. You press Send.",
    availability: "Gmail is optional, and may be limited to invited people while Google reviews the app.",
  },

  practice: {
    questionKicker: "Question, sample",
    question: "Tell me about a time an evaluation changed your design.",
    answerLabel: "Your written answer",
    answer:
      "I built a fixed question set with known good passages, compared recall before and after each change, and read a sample of answers by hand.",
    feedbackKicker: "Written feedback",
    coverageLabel: "Judged by your model",
    coverage: [
      { part: "Situation", present: true },
      { part: "Task", present: true },
      { part: "Action", present: true },
      { part: "Result", present: false },
    ],
    present: "found",
    absent: "not found",
    points: [
      "You named a fixed question set and a before and after comparison.",
      "You described a method but no result. Name the result that changed.",
    ],
    note: "Written feedback on one answer. Not a prediction of how an interview will go.",
  },

  apply: {
    pageKicker: "Northstar Labs application page (sample)",
    pageAddress: "careers.northstar-labs.example/apply",
    formLabel: "A sample application form",
    fields: [
      { label: "Full name", state: "filled" },
      { label: "Email", state: "filled" },
      { label: "Resume", state: "filled" },
      { label: "Country", state: "left" },
      { label: "Demographic and accommodation questions", state: "untouched" },
    ],
    filled: "Filled",
    left: "Left for you",
    untouched: "Not touched",
    // What the extension does with self-identification questions: it never fills or lists them (the extension
    // README, lib/questionSafety.ts), so they are drawn as the form's own and not as part of the panel.
    untouchedNote:
      "The extension does not fill demographic or accommodation questions, and does not list them. They stay yours, so read the whole form before you submit.",
    submit: "Submit",
    submitNote: "Yours to press",
    panelLabel: "A sample of the browser extension's side panel",
    panelKicker: "Browser extension",
    panelFound: "Application found. Ready to fill known fields.",
    panelButton: "Fill this page",
    panelAfter: "After you press it, the panel lists any field it could not fill.",
    panelLeftLabel: "Left empty -- check these yourself",
    // One real left-empty line: the field, "not filled" and the reason, as the extension words them
    // (extension/entrypoints/content.ts noteSkipped; the reason is lib/greenhouse.ts's).
    panelLeft: "Country: not filled -- none of the form's country options matches the country in your profile.",
    panelStops: "Stops here. It never submits.",
    availability: "The extension is for early testers and is not in the Chrome Web Store yet.",
  },
} as const;
