// The application workspace's rows: what the band says on the page a person works in for one role.
// The global rows run first (signed out, setup, no profile, no usable model key, the page's own read
// failing), then these, top to bottom, and the first that applies wins.
//
//   A1c  generating failed in this visit              "Generation didn't finish ..."        Generate again
//   A1b  the latest attempt was held back             "No resume yet: the fit check ..."    Read the fit check
//                                                      (secondary: Generate anyway)
//   A1g  generating, a request is out                 "Generate a tailored resume ..."      Generating...
//   A1   no resume yet                                "Generate a tailored resume ..."      Generate resume
//   A2   a resume with flagged claims                 "Read the {n} flagged claims ..."     Download PDF
//   A2u  a resume whose claim check is not on the   "Your resume is ready to review ..."  Download PDF
//        page (none stored, or it could not be read)
//   A3   a resume with no flagged claims              "No claims flagged. ..."              Download PDF
//   A4   the PDF was downloaded in this visit         "Submit on the employer's site ..."   Open the posting
//   A5   applied                                      "Applied {date}. Waiting on them."    (unlit)
//   A6   screening or interviewing                    "Practice for this interview."        Go to Practice
//   A7   offer                                        "Offer stage. ..."                    (unlit)
//   A8   rejected or withdrawn                        "At rest. ..."                        (unlit)
//
// A1c to A4 are for an application whose status is Saved: that is the stage where a resume is the
// next thing to do. From Applied on, the rows say where the application stands, and a generation that
// fails or is held back is told in the Generate section itself. Every sentence is chosen by code, and
// every fact in a `why` line is something the page read -- nothing is said about a claim check that
// was not stored, a date that was not returned, or a practice session the page never read.
//
// A band never starts, submits or sends anything the person has not asked for: the furthest it goes is
// "Open the posting", and the person submits on the employer's own site.
//
// `workspaceNotFound` is the band for an application the server does not have. It is not a row: with
// no application there is nothing for the setup rows to be about, so the page uses it directly.

import { CROSS_NAV_HASH } from "../../applicationsBoard";
import { definePage, type NextStep, type NextStepRow } from "../types";

// What the page knows about the resume at this moment.
export type GenerationFact =
  /** The stored result is still being read (and no result was made in this visit). */
  | { state: "reading" }
  /** A generation request is out. */
  | { state: "generating" }
  /** The generation request failed in this visit. `at` is the client time, formatted. */
  | { state: "failed"; at: string }
  /** The latest attempt was held back by the fit check: no resume came of it. */
  | { state: "held_back"; ceiling: number | null; earlierResume: boolean }
  /** No resume exists and nothing was held back (or the stored result could not be read). */
  | { state: "none" }
  /** A resume exists. `flagged` is the number of claims the check flagged, or null when this page
   *  has no result to count them in. `unreadable` says why, when it is null: true when a stored result
   *  may exist that the page could not show (its read failed, or a failed run dropped the one on
   *  screen), false when none is stored (a resume from before results were stored). */
  | { state: "drafted"; flagged: number | null; unreadable: boolean; downloaded: boolean };

export type GmailFact = "connected" | "not_connected" | "reconnect_needed" | "unknown";

export interface WorkspaceFacts {
  /** The application's status as returned ("saved", "applied", ...). */
  status: string;
  /** `date_applied` in words ("Sep 22"), or null when it was not returned or cannot be read. */
  appliedOn: string | null;
  /** The posting's address when it is a web address, else null. */
  postingUrl: string | null;
  generation: GenerationFact;
  gmail: GmailFact;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isGeneration(value: unknown): value is GenerationFact {
  if (!isRecord(value)) return false;
  switch (value.state) {
    case "reading":
    case "generating":
    case "none":
      return true;
    case "failed":
      return typeof value.at === "string";
    case "held_back":
      return (value.ceiling === null || (typeof value.ceiling === "number" && Number.isFinite(value.ceiling))) && typeof value.earlierResume === "boolean";
    case "drafted":
      return (value.flagged === null || isCount(value.flagged)) && typeof value.unreadable === "boolean" && typeof value.downloaded === "boolean";
    default:
      return false;
  }
}

export function isWorkspaceFacts(value: unknown): value is WorkspaceFacts {
  if (!isRecord(value)) return false;
  if (typeof value.status !== "string") return false;
  if (value.appliedOn !== null && typeof value.appliedOn !== "string") return false;
  if (value.postingUrl !== null && typeof value.postingUrl !== "string") return false;
  if (!["connected", "not_connected", "reconnect_needed", "unknown"].includes(String(value.gmail))) return false;
  return isGeneration(value.generation);
}

// The intents the page's handlers are registered under. The band only names them.
export const WORKSPACE_INTENTS = {
  generate: "generate",
  generateAnyway: "generate-anyway",
  downloadPdf: "download-pdf",
  readFit: "read-fit",
} as const;

const SAVED = "saved";
const GENERATE_SENTENCE = "Generate a tailored resume for this posting.";

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

const statusWhy = (facts: WorkspaceFacts): string => `status ${facts.status}`;

// Rows that apply only while the status is Saved. A null result is "not this row".
function whileSaved(
  build: (facts: WorkspaceFacts) => NextStep | "wait" | null,
): NextStepRow<WorkspaceFacts>["match"] {
  return (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.status !== SAVED) return null;
    return build(facts);
  };
}

// The stored result decides between A1, A1b, A2 and A3, so nothing is lit until it has been read: a
// guessed first step that is replaced a moment later is worse than a moment of "Loading...".
const A0: NextStepRow<WorkspaceFacts> = {
  id: "A0",
  match: whileSaved((facts) => (facts.generation.state === "reading" ? "wait" : null)),
};

const A1C: NextStepRow<WorkspaceFacts> = {
  id: "A1c",
  match: whileSaved((facts) => {
    const generation = facts.generation;
    if (generation.state !== "failed") return null;
    return {
      sentence: "Generation didn't finish. You didn't do anything wrong, and nothing was lost.",
      action: { kind: "button", label: "Generate again", intent: WORKSPACE_INTENTS.generate },
      why: ["POST /prepare failed", generation.at],
      tone: "failure",
    };
  }),
};

const A1B: NextStepRow<WorkspaceFacts> = {
  id: "A1b",
  match: whileSaved((facts) => {
    const generation = facts.generation;
    if (generation.state !== "held_back") return null;
    const why = ["latest attempt held back"];
    if (generation.ceiling !== null) why.push(`ceiling ${generation.ceiling.toFixed(1)} / 10`);
    if (generation.earlierResume) why.push("earlier resume kept");
    return {
      sentence: generation.earlierResume
        ? "The fit check held the latest attempt back. Your earlier resume is still below."
        : "No resume yet: the fit check held this draft back. Read why, then decide -- your call.",
      action: { kind: "button", label: "Read the fit check", intent: WORKSPACE_INTENTS.readFit },
      secondary: { kind: "button", label: "Generate anyway", intent: WORKSPACE_INTENTS.generateAnyway },
      why,
      // No failure tone: a held-back draft is the engine's judgement, not a failed request, and the
      // honest read below (components/HonestFloor.tsx) is calm on purpose. The red cross is A1c's.
    };
  }),
};

const A1G: NextStepRow<WorkspaceFacts> = {
  id: "A1g",
  match: whileSaved((facts) => {
    if (facts.generation.state !== "generating") return null;
    return {
      sentence: GENERATE_SENTENCE,
      action: { kind: "button", label: "Generate resume", intent: WORKSPACE_INTENTS.generate },
      why: [statusWhy(facts), "generating"],
    };
  }),
};

const A1: NextStepRow<WorkspaceFacts> = {
  id: "A1",
  match: whileSaved((facts) => {
    if (facts.generation.state !== "none") return null;
    return {
      sentence: GENERATE_SENTENCE,
      action: { kind: "button", label: "Generate resume", intent: WORKSPACE_INTENTS.generate },
      why: [statusWhy(facts), "no resume yet", "profile active"],
    };
  }),
};

const DOWNLOAD = { kind: "button", label: "Download PDF", intent: WORKSPACE_INTENTS.downloadPdf } as const;

const A2: NextStepRow<WorkspaceFacts> = {
  id: "A2",
  match: whileSaved((facts) => {
    const generation = facts.generation;
    if (generation.state !== "drafted" || generation.downloaded || generation.flagged === null || generation.flagged === 0) return null;
    const n = generation.flagged;
    return {
      sentence: n === 1 ? "Read the 1 flagged claim, then download." : `Read the ${n} flagged claims, then download.`,
      action: DOWNLOAD,
      why: [statusWhy(facts), "resume drafted", `${n} ${plural(n, "claim", "claims")} flagged`],
    };
  }),
};

const A2U: NextStepRow<WorkspaceFacts> = {
  id: "A2u",
  match: whileSaved((facts) => {
    const generation = facts.generation;
    if (generation.state !== "drafted" || generation.downloaded || generation.flagged !== null) return null;
    return {
      sentence: "Your resume is ready to review. Read it against your own background, then download.",
      action: DOWNLOAD,
      // Two different unknowns, said differently: none stored, against a result the page could not show.
      why: [statusWhy(facts), "resume drafted", generation.unreadable ? "claim check result couldn't be read" : "claim check result not stored"],
    };
  }),
};

const A3: NextStepRow<WorkspaceFacts> = {
  id: "A3",
  match: whileSaved((facts) => {
    const generation = facts.generation;
    if (generation.state !== "drafted" || generation.downloaded || generation.flagged !== 0) return null;
    return {
      sentence: "No claims flagged. Give it one read, then download.",
      action: DOWNLOAD,
      why: [statusWhy(facts), "resume drafted", "0 flagged, not the same as verified"],
    };
  }),
};

const A4: NextStepRow<WorkspaceFacts> = {
  id: "A4",
  match: whileSaved((facts) => {
    const generation = facts.generation;
    if (generation.state !== "drafted" || !generation.downloaded) return null;
    const why = ["PDF downloaded this session", statusWhy(facts)];
    // Without a web address for the posting there is nothing to open: the sentence stands alone.
    if (facts.postingUrl === null) return { sentence: "Submit on the employer's site, then mark it applied.", action: null, why };
    return {
      sentence: "Submit on the employer's site, then mark it applied.",
      action: { kind: "external", label: "Open the posting", href: facts.postingUrl },
      why,
    };
  }),
};

function gmailWhy(facts: WorkspaceFacts): string[] {
  switch (facts.gmail) {
    case "connected":
      return ["a reply may move this automatically, or appear in Today for you to confirm"];
    case "not_connected":
      return ["Gmail not connected -- move it yourself when they reply"];
    case "reconnect_needed":
      return ["Gmail needs reconnecting -- move it yourself when they reply"];
    default:
      // Whether Gmail is open to this person, or connected, is not known: nothing is claimed.
      return [];
  }
}

const A5: NextStepRow<WorkspaceFacts> = {
  id: "A5",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.status !== "applied") return null;
    return {
      sentence: facts.appliedOn === null ? "Applied. Waiting on them." : `Applied ${facts.appliedOn}. Waiting on them.`,
      action: null,
      why: [statusWhy(facts), ...gmailWhy(facts)],
    };
  },
};

// The practice sessions are read by the Practice section, not by this page, so the band cannot say
// whether one exists or is half done: it points at the section and claims nothing about it.
const A6: NextStepRow<WorkspaceFacts> = {
  id: "A6",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || (facts.status !== "screening" && facts.status !== "interviewing")) return null;
    return {
      sentence: "Practice for this interview.",
      action: { kind: "link", label: "Go to Practice", to: `#${CROSS_NAV_HASH.interviewPractice}` },
      why: [statusWhy(facts)],
    };
  },
};

const A7: NextStepRow<WorkspaceFacts> = {
  id: "A7",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.status !== "offer") return null;
    return { sentence: "Offer stage. Nothing for us to draft -- your decision.", action: null, why: [statusWhy(facts)] };
  },
};

const A8: NextStepRow<WorkspaceFacts> = {
  id: "A8",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || (facts.status !== "rejected" && facts.status !== "withdrawn")) return null;
    return { sentence: "At rest. Move it back any time.", action: null, why: [statusWhy(facts)] };
  },
};

export const rules = definePage("workspace", [A0, A1C, A1B, A1G, A1, A2, A2U, A3, A4, A5, A6, A7, A8], { facts: isWorkspaceFacts });

// The band of a page whose application does not exist (a 404 on its read). Retrying cannot help, so
// the only step is back to the list.
export function workspaceNotFound(applicationId: string): NextStep {
  return {
    sentence: "This application wasn't found -- it may have been deleted.",
    action: { kind: "link", label: "Go to Applications", to: "/applications" },
    why: [`GET /applications/${applicationId} returned not found`],
    tone: "failure",
  };
}
