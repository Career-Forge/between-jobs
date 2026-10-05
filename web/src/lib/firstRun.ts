// The first-run checklist on Today: five steps that take a new account from "just signed
// up" to "has generated a resume", ticked from what the account actually has.
//
// WHAT TICKS A STEP. Only real data, never a click: an active profile exists; a validated
// model key exists; a first search happened; an application is tracked; an application has
// a resume. Each comes from a list the app already reads (the profile, the credentials,
// the applications with their `resume_exists`, the saved searches) -- no route was added
// for this, and nothing here calls a rate-limited endpoint: the checklist runs on every
// visit to Today, so it may only make cheap GETs, and `/discover` (a full search that
// spends a slot of the user's hourly limit) is not one of them.
//
// "FIRST SEARCH" LEAVES NO ROW. A search is not persisted, and the only route that runs
// one is the rate-limited /discover, so it cannot be read back. It counts as done when this
// browser remembers a first successful search (the Discover page sets that flag, per user,
// after its first search that returned), or when the account has what only a search leaves
// behind: an application tracked FROM Discover (source_channel "discover" -- one pasted on
// Applications, added by URL or sent over Telegram says nothing about a search), or a saved
// search. A saved search is the weaker of the two: Discover will save one with empty
// filters before anything has been searched. It is kept because a saved search almost
// always follows a search, and the browser flag does not travel to another browser.
//
// THREE STATES, NEVER TWO. A step is done, todo, or UNKNOWN. An input that could not be
// loaded (a network blip, a server error) makes the steps that depend on it unknown --
// shown neutrally -- and never done and never todo: guessing "todo" would nag someone who
// finished that step, and guessing "done" would hide one they did not.
//
// THE CARD IS FOR ACCOUNTS THAT NEED GUIDANCE. It shows only while some step is KNOWN to
// be todo. An unknown step alone is not a reason to show it: an account that finished
// everything must not be told to redo steps because one of the four requests failed. And
// once an account is seen with all five done, this browser remembers it (per user) and the
// requests stop altogether -- the checklist runs on every visit to Today, so a finished
// account would otherwise pay for four requests, one of them heavy, to draw nothing.
//
// Pure module: the fetcher and the storage are injected (api.ts pulls in the Supabase
// client, which throws at import time without env vars, and storage may be missing or may
// throw), and there is no React here -- the hook is useFirstRun.ts and the card is
// components/FirstRunChecklist.tsx.

import { CROSS_NAV_HASH } from "./applicationsBoard";

// ── the facts ──────────────────────────────────────────────────────────────

// An input to the checklist: still being asked, answered, or failed to answer.
export type Fetched<T> =
  | { kind: "loading" }
  | { kind: "ok"; value: T }
  | { kind: "failed" };

export interface ApplicationFacts {
  count: number;
  // How many were tracked from a Discover search (source_channel "discover"): the only
  // applications that show a search ran.
  fromDiscover: number;
  // How many have a resume generated.
  withResume: number;
  // An application to open to generate a resume (the first without one, else the first),
  // so the step can link straight to its Generate panel; null when none has an id.
  resumeTargetId: string | null;
}

export interface FirstRunFacts {
  // An active profile exists.
  profile: Fetched<boolean>;
  // A validated key for the model exists.
  modelKey: Fetched<boolean>;
  savedSearches: Fetched<number>;
  applications: Fetched<ApplicationFacts>;
}

// What this browser knows, apart from the API.
export interface FirstRunFlags {
  dismissed: boolean;
  // This browser remembers a first successful search: true; remembers none: false;
  // storage could not be read: null (unknown).
  searched: boolean | null;
}

const FAILED = { kind: "failed" } as const;

function ok<T>(value: T): Fetched<T> {
  return { kind: "ok", value };
}

// ── loading ────────────────────────────────────────────────────────────────

// `apiFetch`'s shape for a plain GET.
export type Fetcher = <T>(path: string) => Promise<T>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function statusOf(error: unknown): number | null {
  const status = error instanceof Error ? (error as { status?: unknown }).status : undefined;
  return typeof status === "number" ? status : null;
}

async function settle<T>(read: () => Promise<Fetched<T>>): Promise<Fetched<T>> {
  try {
    return await read();
  } catch {
    return FAILED;
  }
}

// The rows of a list reply, or null when it is not a list.
async function readRows(fetcher: Fetcher, path: string): Promise<Record<string, unknown>[] | null> {
  const body = await fetcher<unknown>(path);
  if (!Array.isArray(body)) return null;
  return body.filter(isRecord);
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

// The four requests, all plain GETs the app already makes elsewhere (Profile, Integrations,
// Applications, Discover's saved searches). Each fails on its own: one fact failing leaves
// the other three.
export async function loadFirstRunFacts(fetcher: Fetcher): Promise<FirstRunFacts> {
  const [profile, modelKey, savedSearches, applications] = await Promise.all([
    settle<boolean>(async () => {
      try {
        return isRecord(await fetcher<unknown>("/profile/current")) ? ok(true) : FAILED;
      } catch (e) {
        // "No active profile yet" is a 404, and is the answer, not a failure.
        return statusOf(e) === 404 ? ok(false) : FAILED;
      }
    }),
    settle<boolean>(async () => {
      const rows = await readRows(fetcher, "/credentials");
      if (rows === null) return FAILED;
      return ok(rows.some((row) => row.service === "llm" && row.is_validated === true));
    }),
    settle<number>(async () => {
      const rows = await readRows(fetcher, "/saved-searches");
      return rows === null ? FAILED : ok(rows.length);
    }),
    settle<ApplicationFacts>(async () => {
      const rows = await readRows(fetcher, "/applications");
      if (rows === null) return FAILED;
      const withoutResume = rows.find((row) => row.resume_exists !== true && nonEmptyString(row.id));
      const first = rows.find((row) => nonEmptyString(row.id));
      return ok({
        count: rows.length,
        fromDiscover: rows.filter((row) => row.source_channel === "discover").length,
        withResume: rows.filter((row) => row.resume_exists === true).length,
        resumeTargetId: nonEmptyString((withoutResume ?? first)?.id),
      });
    }),
  ]);
  return { profile, modelKey, savedSearches, applications };
}

// ── deriving the steps ─────────────────────────────────────────────────────

export type StepId = "profile" | "model_key" | "first_search" | "track_job" | "generate_resume";
export type StepStatus = "done" | "todo" | "unknown";

export interface FirstRunStep {
  id: StepId;
  label: string;
  hint: string;
  // The page where the step is done.
  to: string;
  status: StepStatus;
}

export interface FirstRunView {
  // Whether the card is shown at all.
  visible: boolean;
  steps: readonly FirstRunStep[];
  doneCount: number;
  // The first step KNOWN to be todo, or null when there is none. A step that could not be
  // checked is never named as the next one: the card would be telling the person to do
  // something it does not know they have not done.
  nextStep: FirstRunStep | null;
}

function fromFlag(fact: Fetched<boolean>): StepStatus {
  if (fact.kind !== "ok") return "unknown";
  return fact.value ? "done" : "todo";
}

function firstSearchStatus(facts: FirstRunFacts, flags: FirstRunFlags): StepStatus {
  if (flags.searched === true) return "done";
  if (facts.savedSearches.kind === "ok" && facts.savedSearches.value > 0) return "done";
  if (facts.applications.kind === "ok" && facts.applications.value.fromDiscover > 0) return "done";
  // Nothing shows a search. That is "not yet" only if everything that could have shown one
  // was actually read; otherwise it is simply not known.
  const readEverything =
    facts.savedSearches.kind === "ok" && facts.applications.kind === "ok" && flags.searched === false;
  return readEverything ? "todo" : "unknown";
}

function resumeLink(facts: FirstRunFacts): string {
  if (facts.applications.kind === "ok" && facts.applications.value.resumeTargetId !== null) {
    return `/applications/${encodeURIComponent(facts.applications.value.resumeTargetId)}#${CROSS_NAV_HASH.generate}`;
  }
  return "/applications";
}

export function deriveFirstRun(facts: FirstRunFacts, flags: FirstRunFlags): FirstRunView {
  const applications = facts.applications;
  const steps: FirstRunStep[] = [
    {
      id: "profile",
      label: "Add your profile",
      hint: "Import your resume as a profile. Everything else is built from it.",
      to: "/profile",
      status: fromFlag(facts.profile),
    },
    {
      id: "model_key",
      label: "Add a model key",
      hint: "Paste your own model key. This app never runs on a shared key.",
      to: "/profile/integrations",
      status: fromFlag(facts.modelKey),
    },
    {
      id: "first_search",
      label: "Run a first search",
      hint: "Search for a role, or leave the filters empty to browse the latest postings.",
      to: "/discover",
      status: firstSearchStatus(facts, flags),
    },
    {
      id: "track_job",
      label: "Track a job",
      hint: "Track one from a search, or paste a posting on Applications.",
      to: "/applications",
      status: applications.kind === "ok" ? (applications.value.count > 0 ? "done" : "todo") : "unknown",
    },
    {
      id: "generate_resume",
      label: "Generate a resume",
      hint: "Open a tracked job and press Generate resume.",
      to: resumeLink(facts),
      status:
        applications.kind === "ok" ? (applications.value.withResume > 0 ? "done" : "todo") : "unknown",
    },
  ];

  const doneCount = steps.filter((step) => step.status === "done").length;
  const stillLoading = Object.values(facts).some((fact) => fact.kind === "loading");
  const nextStep = steps.find((step) => step.status === "todo") ?? null;
  return {
    visible: !flags.dismissed && !stillLoading && nextStep !== null,
    steps,
    doneCount,
    nextStep,
  };
}

// ── what this browser remembers ────────────────────────────────────────────

// `localStorage`'s shape, as much of it as is used. Storage can be missing (a private
// window, blocked site data) or can throw on any read or write, so every use below is in a
// try/catch and the card renders correctly without it.
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

// The browser's localStorage, or null when there is none or reaching for it throws (some
// browsers throw from the `localStorage` property itself when site data is blocked).
export function browserStorage(
  get: () => StorageLike | null | undefined = () => window.localStorage,
): StorageLike | null {
  try {
    return get() ?? null;
  } catch {
    return null;
  }
}

const FLAG_SET = "1";

// Per user, so one account's choices on a shared browser do not decide another's.
export function dismissedKey(userId: string): string {
  return `between-jobs:first-run:dismissed:${userId}`;
}

export function searchedKey(userId: string): string {
  return `between-jobs:first-run:searched:${userId}`;
}

// A dismissed card comes back only if the person clears it (their browser's site data);
// nothing here brings it back on its own. When storage cannot be read it is not dismissed.
export function readDismissed(storage: StorageLike | null, userId: string): boolean {
  try {
    return storage?.getItem(dismissedKey(userId)) === FLAG_SET;
  } catch {
    return false;
  }
}

// Whether the flag was stored; the caller hides the card either way for this visit.
export function writeDismissed(storage: StorageLike | null, userId: string): boolean {
  try {
    if (storage === null) return false;
    storage.setItem(dismissedKey(userId), FLAG_SET);
    return true;
  } catch {
    return false;
  }
}

// Whether this browser remembers a first successful search: true or false, and null when
// it cannot be read at all (unknown, which the step then shows as such).
export function readSearched(storage: StorageLike | null, userId: string): boolean | null {
  try {
    if (storage === null) return null;
    return storage.getItem(searchedKey(userId)) === FLAG_SET;
  } catch {
    return null;
  }
}

// Called by the Discover page after a search that returned. Best effort.
export function markSearched(storage: StorageLike | null, userId: string): void {
  try {
    storage?.setItem(searchedKey(userId), FLAG_SET);
  } catch {
    // Nothing useful to do: the step then rests on what the account holds (a saved search,
    // an application tracked from Discover).
  }
}

// An account seen with all five steps done. Remembered per user, per browser, so a finished
// account neither makes the four requests again nor gets the card back because one of them
// failed (or because an application was later deleted). A browser that has lost it (site
// data cleared, a new browser) costs one more check, which sets it again.
export function completedKey(userId: string): string {
  return `between-jobs:first-run:completed:${userId}`;
}

export function readCompleted(storage: StorageLike | null, userId: string): boolean {
  try {
    return storage?.getItem(completedKey(userId)) === FLAG_SET;
  } catch {
    return false;
  }
}

// Best effort: when it cannot be stored the account is simply checked again next visit.
export function writeCompleted(storage: StorageLike | null, userId: string): boolean {
  try {
    if (storage === null) return false;
    storage.setItem(completedKey(userId), FLAG_SET);
    return true;
  } catch {
    return false;
  }
}

// ── the checklist as one small machine ─────────────────────────────────────

// Everything useFirstRun.ts keeps, and every decision it makes, so that the hook is only
// wiring: what is read from storage and under which person's key, what is written when the
// card is dismissed or the account is seen finished, whether anything is asked of the API
// at all, and what the card then shows. Each is a plain function over an injected storage.
export interface FirstRunState {
  dismissed: boolean;
  completed: boolean;
  facts: FirstRunFacts;
  // What this browser remembered about a first search when the facts arrived.
  searched: boolean | null;
}

export const STILL_LOADING: FirstRunFacts = {
  profile: { kind: "loading" },
  modelKey: { kind: "loading" },
  savedSearches: { kind: "loading" },
  applications: { kind: "loading" },
};

export function initialFirstRunState(storage: StorageLike | null, userId: string): FirstRunState {
  return {
    dismissed: readDismissed(storage, userId),
    completed: readCompleted(storage, userId),
    facts: STILL_LOADING,
    searched: null,
  };
}

// A card that is dismissed, or that belongs to an account already seen finished, is never
// shown, so nothing is asked of the API for it.
export function shouldLoadFirstRun(state: FirstRunState): boolean {
  return !state.dismissed && !state.completed;
}

// What to take from a load that has arrived. The browser's first-search flag is read now,
// not at mount: the Discover page sets it, and a fresh visit to Today runs this again. An
// account with all five steps done is written down as completed, from this answer only.
export function onFirstRunFactsLoaded(
  facts: FirstRunFacts,
  storage: StorageLike | null,
  userId: string,
): Pick<FirstRunState, "facts" | "searched" | "completed"> {
  const searched = readSearched(storage, userId);
  const view = deriveFirstRun(facts, { dismissed: false, searched });
  const finished = view.doneCount === view.steps.length;
  if (finished) writeCompleted(storage, userId);
  return { facts, searched, completed: finished };
}

// Hidden for this visit whether or not the flag could be stored.
export function onFirstRunDismissed(
  state: FirstRunState,
  storage: StorageLike | null,
  userId: string,
): FirstRunState {
  writeDismissed(storage, userId);
  return { ...state, dismissed: true };
}

export function viewOfFirstRun(state: FirstRunState): FirstRunView {
  return deriveFirstRun(state.facts, {
    dismissed: state.dismissed || state.completed,
    searched: state.searched,
  });
}
