// The trail lines and receipts of the workspace's research and prepare sections, built only from fields
// the API returned (lib/trail.ts is the grammar: a slot whose field did not come back is left out with
// its separator, and a line with no words left is not drawn).
//
//   Company intel   "✦ Claims written by your model · from You.com results, on your keys · Sep 20"
//   Contacts        "✦ People named by your model · from You.com results, on your keys · Sep 20"
//   Events          "✦ Events picked out by your model · from You.com results, on your keys · Sep 20"
//   a run that found nothing   "○ Searched with You.com · on your keys · Sep 20 · ✕ 7 searches failed or skipped"
//   an outreach draft   "✦ Drafted by your model · on your model key · Sep 20"
//   a practice answer   "✦ Scored by your model · on your model key · Sep 21"
//   a practice session  "✦ Questions asked by your model · from your resume evidence · Sep 21"
//   Your play       "✦ Written by your model · on your model key · Sep 20"
//
// Three things are never written, because the response does not carry them: which model ran (credentials
// resolve per capability, so the model chosen in Settings may not be the one that ran), the provider the
// person's model key belongs to, and a time for anything the browser made up. A just-submitted practice
// answer is answered by the server without a time, so its line has no "when".

import { searchesFailedWords, failedSearchCount, providersPhrase, runDate, type ResearchRun } from "./research";
import { providerLabel as searchProviderLabel } from "./hiringSignals";
import type { SearchResponse } from "./hiringSignalsTypes";
import type { InterviewQuestion, InterviewSession } from "./interviewPracticeTypes";
import type { ReceiptRowInput, TrailInput } from "./trail";
import { formatTrailDate } from "./trail";

export interface ResearchTrailParts {
  trail: TrailInput;
  receipt: readonly ReceiptRowInput[];
  receiptTitle: string;
}

const RUN_PREFIX = 8;

function prefix(value: unknown, length: number): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim().slice(0, length) : null;
}

// ── a research run ────────────────────────────────────────────────────────

interface RunTrailOptions {
  /** "Claims written by your model" */
  actor: string;
  /** What a run holds, for the receipt: ["claims", 7]. */
  count: { label: string; value: number };
  /** The sentence a receipt adds about how the output was checked, when there is one. */
  notes: readonly string[];
  now: Date;
  timeZone?: string;
}

// Providers that need no key of the person's: GitHub's public member list is read unauthenticated, so
// a line that mentions it does not say the results came "on your keys".
const KEYLESS_PROVIDERS: ReadonlySet<string> = new Set(["github"]);

function runTrail(run: ResearchRun, options: RunTrailOptions): ResearchTrailParts {
  const providers = providersPhrase(run.providers_used);
  const failed = failedSearchCount(run);
  const onYourKeys = !run.providers_used.some((id) => KEYLESS_PROVIDERS.has(id));
  const keyless = run.providers_used.includes("github");
  const also = failed > 0 ? { glyph: "failed" as const, text: searchesFailedWords(failed) } : null;
  const when = runDate(run, options.now, options.timeZone);
  // A run that came back with nothing made nothing for a model to have written, whether or not it was
  // asked (the backend skips the model when every search failed, and drops what it cannot source). So the
  // line says only what is true of such a run: what searched.
  const empty = options.count.value === 0;
  const trail: TrailInput = empty
    ? {
        glyph: "code",
        actor: providers === null ? null : `Searched with ${providers}`,
        basis: providers !== null && onYourKeys ? "on your keys" : null,
        when,
        also,
      }
    : {
        glyph: "model",
        actor: options.actor,
        basis: providers === null ? "on your keys" : onYourKeys ? `from ${providers} results, on your keys` : `from ${providers} results`,
        when,
        also,
      };
  return {
    trail,
    receipt: [
      { label: "company", value: run.company_name },
      { label: "searched with", value: providers },
      { label: options.count.label, value: options.count.value },
      { label: "searches failed or skipped", value: failed > 0 ? failed : null },
      { label: "run", value: prefix(run.id, RUN_PREFIX) },
      ...(empty ? [] : options.notes).map((note) => ({ note })),
      { note: keyless ? "GitHub's public member list needs no key, so those results did not use one." : null },
    ],
    receiptTitle: "Where this research came from",
  };
}

export function intelTrail(run: ResearchRun, claimCount: number, now: Date, timeZone?: string): ResearchTrailParts {
  return runTrail(run, {
    actor: "Claims written by your model",
    count: { label: "claims", value: claimCount },
    notes: [
      "Each claim links to a result the search returned. The confidence beside it is your model's own reading, not a check.",
    ],
    now,
    timeZone,
  });
}

export function contactsTrail(run: ResearchRun, candidateCount: number, now: Date, timeZone?: string): ResearchTrailParts {
  return runTrail(run, {
    actor: "People named by your model",
    count: { label: "people", value: candidateCount },
    notes: [
      "Code checked that each name appears in the title or snippet of the source it cites. The order is code's too: the kind of role, how many sources name the person and whether a source says it is hiring.",
      "No email address or phone number comes with a run.",
    ],
    now,
    timeZone,
  });
}

export function eventsTrail(run: ResearchRun, eventCount: number, now: Date, timeZone?: string): ResearchTrailParts {
  return runTrail(run, {
    actor: "Events picked out by your model",
    count: { label: "events", value: eventCount },
    notes: [
      "A speaker is named only when the source's own text names them. Otherwise the event is listed as company-adjacent.",
      "No one's attendance is guessed.",
    ],
    now,
    timeZone,
  });
}

// A section that could not be read, or a run that did not finish with nothing earlier to show: the
// failed block's own line, and nothing else. What went wrong is the callout's, in the server's own words.
export function failedTrail(sentence: string): ResearchTrailParts {
  return { trail: { glyph: "failed", actor: sentence }, receipt: [], receiptTitle: "What went wrong" };
}

// ── an outreach draft ─────────────────────────────────────────────────────

// `when` is the draft row's own `created_at`, which the draft-outreach reply and the push-to-Gmail reply
// both carry. A row without one (an untyped reply) gets a line with no time, as the omit rule says.
export function draftTrail(draft: { created_at?: string | null }, now: Date, timeZone?: string): ResearchTrailParts {
  return {
    trail: { glyph: "model", actor: "Drafted by your model", basis: "on your model key", when: formatTrailDate(draft.created_at, { now, timeZone }) },
    receipt: [
      { note: "Nothing is sent. Between Jobs only writes the draft: you copy it, open it in your mail app, or save the email as a draft in your own Gmail when that is set up for you." },
    ],
    receiptTitle: "What this draft is",
  };
}

// ── interview practice ────────────────────────────────────────────────────

// A question's answer, scored by the person's model. `when` is the row's own `answered_at` and is
// only passed for an answer the server stored on an earlier visit: one submitted in this visit came
// back without a time.
export function answerTrail(question: Pick<InterviewQuestion, "id" | "answered_at" | "score">, now: Date, savedEarlier: boolean, timeZone?: string): ResearchTrailParts {
  return {
    trail: {
      glyph: "model",
      actor: "Scored by your model",
      basis: "on your model key",
      when: savedEarlier ? formatTrailDate(question.answered_at, { now, timeZone }) : null,
    },
    receipt: [
      { label: "question", value: prefix(question.id, RUN_PREFIX) },
      { note: "The score, the STAR parts and the comments are your model's judgment of your answer. Code only checks that the reply has the expected shape." },
    ],
    receiptTitle: "Where this score came from",
  };
}

// The session's questions. When any is grounded in interview-process research for the company, the
// receipt says that research is shared: it is stored once per company, not per person, and may have
// been written on another person's key.
export function sessionTrail(session: InterviewSession, questions: readonly InterviewQuestion[], now: Date, timeZone?: string): ResearchTrailParts {
  const grounded = questions.some((question) => question.grounded_in !== null && question.grounded_in !== "") || session.registry_entry_id !== null;
  return {
    trail: {
      glyph: "model",
      actor: "Questions asked by your model",
      basis: "from your resume evidence",
      when: formatTrailDate(session.started_at, { now, timeZone }),
    },
    receipt: [
      { label: "session", value: prefix(session.id, RUN_PREFIX) },
      { label: "status", value: session.status === "completed" ? "completed" : session.status === "in_progress" ? "in progress" : null },
      { label: "questions", value: questions.length === 0 ? null : questions.length },
      { note: grounded ? "Some questions are grounded in interview-process research for this company. That research is shared across users, and it may have been written on another person's model key." : null },
    ],
    receiptTitle: "Where these questions came from",
  };
}

// ── Your play ─────────────────────────────────────────────────────────────

export interface BriefFacts {
  id: string;
  created_at: string;
  rubric_warnings: readonly string[];
}

export function briefTrail(brief: BriefFacts, now: Date, timeZone?: string): ResearchTrailParts {
  return {
    trail: {
      glyph: "model",
      actor: "Written by your model",
      basis: "on your model key",
      when: formatTrailDate(brief.created_at, { now, timeZone }),
      extra: brief.rubric_warnings.length > 0 ? `${brief.rubric_warnings.length} flagged` : null,
    },
    receipt: [
      { label: "brief", value: prefix(brief.id, RUN_PREFIX) },
      { label: "flagged by code", value: brief.rubric_warnings.length > 0 ? brief.rubric_warnings.length : null },
      { note: "Code checked that each citation is one of the strengths or gaps found for this role. It is not a judgment that the advice is right." },
      { note: "A flag marks wording that promises an outcome; nothing is removed." },
    ],
    receiptTitle: "Where this brief came from",
  };
}

// ── hiring posts ──────────────────────────────────────────────────────────

// What searched, for the block of results: the person's own search provider, with code tagging each post
// (a pattern match over its wording) and no model anywhere in it. The query's own wording is the
// results' first line, so it is not repeated here. No time came back with a search: a post's own time is
// read off its address and shown on the post.
export function hiringResultsTrail(response: Pick<SearchResponse, "provider" | "cached" | "signals">): ResearchTrailParts {
  return {
    trail: {
      glyph: "code",
      actor: `via ${searchProviderLabel(response.provider)}`,
      basis: "tagged by code, no model involved",
      extra: response.cached ? "cached result" : null,
    },
    receipt: [
      { label: "posts shown", value: response.signals.length },
      { note: "Nothing is loaded from a post's own site until you press Show post." },
    ],
    receiptTitle: "Where these posts came from",
  };
}

// The posts the person saved to this application: their own action, and a saved post is only a pointer.
export function hiringSavedTrail(saved: number): ResearchTrailParts {
  return {
    trail: { glyph: "you", actor: "You saved these", extra: saved === 1 ? "1 post" : `${saved} posts` },
    receipt: [{ note: "A save keeps a pointer to the post, not its text." }],
    receiptTitle: "What a saved post is",
  };
}
