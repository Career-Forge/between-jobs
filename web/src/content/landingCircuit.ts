// The five stages of the landing page's circuit, as data. Every card is built from a fictional
// sample role and is labeled as one on the page. What each stage says was checked against the
// product: Discover rates a result from the model's sub-scores with code; the resume checklist has
// checks that code ran and checks left to you (api/export_checklist.py); outreach is drafted by
// your model and, with the optional Gmail connection (a server may limit it to invited people), lands in your
// drafts; practice feedback is the model's; the extension fills by plain code and never clicks Next
// or Submit. A sample never carries a date, a number or a count, because none was read from anywhere.

import type { TrailInput } from "../lib/trail";

export type StageId = "find" | "prepare" | "reach" | "practice" | "apply";

interface StageBase {
  id: StageId;
  label: string;
  /** What this stage does, in one plain sentence. Shown under the stage on a phone. */
  summary: string;
  /** The sample next step for this stage. `action` is drawn as an outlined mock, never a control. */
  demo: { sentence: string; why: readonly string[]; action: string | null };
  /** The one trail line the card ends in. */
  trail: TrailInput;
}

export interface FindStage extends StageBase {
  id: "find";
  card: { kicker: string; fit: string; fitFilled: number; fitCells: number; lowestLabel: string; lowest: string; mockAction: string };
}

export interface PrepareStage extends StageBase {
  id: "prepare";
  card: {
    kicker: string;
    title: string;
    groups: readonly { label: string; items: readonly { mark: "✓" | "○"; text: string; state: string }[] }[];
  };
}

export interface ReachStage extends StageBase {
  id: "reach";
  card: { kicker: string; to: string; subject: string; note: string; badge: string };
}

export interface PracticeStage extends StageBase {
  id: "practice";
  card: { kicker: string; question: string; feedbackLabel: string; feedback: string; note: string };
}

export interface ApplyStage extends StageBase {
  id: "apply";
  card: { kicker: string; page: string; mockAction: string; leftLabel: string; left: string };
}

export type Stage = FindStage | PrepareStage | ReachStage | PracticeStage | ApplyStage;

export const STAGES: readonly Stage[] = [
  {
    id: "find",
    label: "Find a role",
    summary:
      "Search company job boards and the search providers you connect. Your model rates the top results against your profile, and you track the ones worth a draft.",
    demo: {
      sentence: "Track the jobs worth a draft.",
      why: ["sample results", "scored against your profile"],
      action: null,
    },
    card: {
      kicker: "Discover",
      fit: "Good",
      fitFilled: 3,
      fitCells: 4,
      lowestLabel: "Lowest part",
      lowest: "Skills",
      mockAction: "Track",
    },
    trail: { glyph: "engine", actor: "code-weighted from your model's sub-scores" },
  },
  {
    id: "prepare",
    label: "Prepare",
    summary:
      "Draft a resume, and a cover letter if you want one, from your own profile. A checklist keeps what code checked apart from what is left for you.",
    demo: {
      sentence: "Read any flagged claims, then download.",
      why: ["status saved", "resume drafted"],
      action: "Download PDF",
    },
    card: {
      kicker: "Resume checklist",
      title: "Tailored resume",
      groups: [
        {
          label: "Checked by Between Jobs",
          items: [
            { mark: "✓", text: "Fits on one page", state: "Pass" },
            { mark: "✓", text: "No engine warnings", state: "Pass" },
          ],
        },
        {
          label: "Yours to check",
          items: [
            { mark: "○", text: "Links are valid", state: "Not checked" },
            { mark: "○", text: "Fonts are embedded", state: "Not checked" },
          ],
        },
      ],
    },
    trail: { glyph: "code", actor: "Checked by code", basis: "no model involved" },
  },
  {
    id: "reach",
    label: "Reach people",
    summary:
      "With your own search keys, find people at the company and draft a short note to one of them. If you connect Gmail, the draft lands in your Drafts folder and you press Send.",
    demo: {
      sentence: "Read the draft in your mailbox, then send it yourself.",
      why: ["draft created", "sending is yours"],
      action: null,
    },
    card: {
      kicker: "Contacts and outreach",
      to: "To: a recruiter at Northstar Labs (sample)",
      subject: "Applied AI Engineer: a short introduction",
      note: "Waiting in your Gmail drafts. You press Send.",
      badge: "Gmail is optional, and may be invite-only for now",
    },
    trail: { glyph: "model", actor: "Drafted by your model", basis: "on your key", extra: "never sent by Between Jobs" },
  },
  {
    id: "practice",
    label: "Practice",
    summary:
      "Practice questions for the role, grounded in your resume evidence and, when one was found, a cited brief of how the company interviews. Each answer gets written feedback.",
    demo: {
      sentence: "Practice for this interview.",
      why: ["status interviewing", "no practice session yet"],
      action: "Start practice session",
    },
    card: {
      kicker: "Practice",
      question: "Tell me about a time an evaluation changed your design.",
      feedbackLabel: "Feedback",
      feedback: "Name the result that changed.",
      note: "Written feedback on one answer. Not a prediction of how an interview will go.",
    },
    trail: { glyph: "model", actor: "Scored by your model", basis: "on your key" },
  },
  {
    id: "apply",
    label: "Apply",
    summary:
      "The browser extension fills an application page from your profile when you press Fill, then stops. You check each field and press Submit yourself.",
    demo: {
      sentence: "Application found. Fill the fields we know.",
      why: ["tracked application matches this page"],
      action: "Fill this page",
    },
    card: {
      kicker: "Browser extension",
      page: "Northstar Labs application page (sample)",
      mockAction: "Fill this page",
      leftLabel: "Left for you",
      left: "demographic and accommodation questions, and Submit",
    },
    trail: { glyph: "code", actor: "Filled by code", basis: "from your profile", extra: "submit is yours" },
  },
];

// The statuses a tracked role can sit in on the board, in pipeline order, with the shape each one
// is drawn with. Offer is the only one that is ever green.
export const BOARD_STATUSES = [
  { label: "Saved", mark: "◇" },
  { label: "Applied", mark: "●" },
  { label: "Screening", mark: "◔" },
  { label: "Interviewing", mark: "◑" },
  { label: "Offer", mark: "✓" },
] as const;
