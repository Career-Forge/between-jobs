// The first-run checklist on Today: five steps that take a new account from "just signed
// up" to "has generated a resume", ticked from what the account actually has.
//
// WHAT TICKS A STEP. Only real data, never a click: an active profile exists; a validated
// model key with a model chosen exists (a key that is checked and has no model is saved and
// unusable, so it does not tick the step, and the step then says what is left: "Choose a model",
// with a link of its own); a first search happened; an application is tracked; an application has
// a resume. Each comes from a list the app already reads (the profile, the credentials,
// the applications with their `resume_exists`, the saved searches) -- no route was added
// for this, and nothing here calls a rate-limited endpoint: the reads run on every
// visit to Today, so they may only be cheap GETs, and `/discover` (a full search that
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
// THE CARD OPENS FOR ACCOUNTS THAT NEED GUIDANCE. It is drawn open only while some step is KNOWN
// to be todo, and only until it is hidden. An unknown step alone is not a reason to open it: an
// account that finished everything must not be told to redo steps because one of the four requests
// failed. Otherwise it is one line that opens on a click -- "Setup ready" when all five are done,
// the counts when it was hidden or only unknowns remain -- so the person can always get back to it.
// Once an account is seen with all five done, this browser remembers it (per user) so the next visit
// keeps a place for the one line while the reads are out. That line says only "Setup" and that it is
// checking: it claims nothing the reads have not confirmed, and the reads then decide what it says
// ("Setup ready", the counts, or the open list).
//
// The reads are Today's (readAccount): it needs profile, credentials, saved searches and
// applications for the band, the background trail and the sentences' job names as well, so the
// checklist takes them from there rather than asking for its own.
//
// THE CHAT BOT HAS A COPY OF THESE RULES. Its `/learn` command shows the same five steps, derived in
// src/between_jobs/api/first_run.py from what the account holds. The two are held to one table of
// accounts and the steps each must come out with (tests/shared/first_run_steps.json), run by
// firstRunShared.test.ts here and by tests/test_first_run.py there. Change a rule in either place
// and update the table and the other side.
//
// Pure module: the fetcher and the storage are injected (api.ts pulls in the Supabase
// client, which throws at import time without env vars, and storage may be missing or may
// throw), and there is no React here -- the hook is useFirstRun.ts and the card is
// components/FirstRunChecklist.tsx.

import { CROSS_NAV_HASH } from "./applicationsBoard";
import { CONNECT_MODEL_WORDS, MODEL_CHOICE_LABEL, modelKeyDetailOf, modelKeyOf, modelStatusWords, type ModelKeyDetail } from "./modelKey";
import { MODEL_PROVIDER_PATH } from "./settingsPaths";

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
  // When that profile was activated, as GET /profile/current returned it; absent or null when it did
  // not, and the step then says nothing of a date.
  profileActiveSince?: string | null;
  // A validated key with a model chosen exists.
  modelKey: Fetched<boolean>;
  // A key is checked and saved with no model chosen, and no key is ready. Only read when modelKey is
  // false. Absent means no key is waiting for a model.
  modelKeyNeedsModel?: Fetched<boolean>;
  // Which providers are behind the model key reading, from the same credentials rows. Only the words
  // of the step's description line use it (the shared label and the chat's copy do not), and it is
  // absent when the credentials were not read.
  modelKeyDetail?: ModelKeyDetail;
  savedSearches: Fetched<number>;
  applications: Fetched<ApplicationFacts>;
}

// What this browser knows, apart from the API.
export interface FirstRunFlags {
  dismissed: boolean;
  // This browser has seen the account with all five done (written when the facts last said so). It
  // keeps a place for the one setup line while the reads are out; it never counts a step and it
  // never lets the line say the account is ready.
  seenFinished?: boolean;
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

// What the four reads returned, before anything is decided from them. Today draws more than the
// checklist from these (the band's setup rows, the saved searches in the background trail, the
// application names in a sentence), so they are read once, here, and each consumer takes what it needs.
export interface AccountReads {
  // An active profile: `active` false is the 404 ("no active profile yet"), which is the answer, not a failure.
  profile: Fetched<{ active: boolean; activatedAt: string | null }>;
  credentials: Fetched<readonly Record<string, unknown>[]>;
  savedSearches: Fetched<readonly Record<string, unknown>[]>;
  applications: Fetched<readonly Record<string, unknown>[]>;
}

export const ACCOUNT_STILL_LOADING: AccountReads = {
  profile: { kind: "loading" },
  credentials: { kind: "loading" },
  savedSearches: { kind: "loading" },
  applications: { kind: "loading" },
};

// The four requests, all plain GETs the app already makes elsewhere (Profile, Settings,
// Applications, Discover's saved searches). Each fails on its own: one fact failing leaves
// the other three.
export async function readAccount(fetcher: Fetcher): Promise<AccountReads> {
  const [profile, credentials, savedSearches, applications] = await Promise.all([
    settle<{ active: boolean; activatedAt: string | null }>(async () => {
      try {
        const body = await fetcher<unknown>("/profile/current");
        if (!isRecord(body)) return FAILED;
        return ok({ active: true, activatedAt: nonEmptyString(body.activated_at) });
      } catch (e) {
        // "No active profile yet" is a 404, and is the answer, not a failure.
        return statusOf(e) === 404 ? ok({ active: false, activatedAt: null }) : FAILED;
      }
    }),
    settle(async () => {
      const rows = await readRows(fetcher, "/credentials");
      return rows === null ? FAILED : ok(rows);
    }),
    settle(async () => {
      const rows = await readRows(fetcher, "/saved-searches");
      return rows === null ? FAILED : ok(rows);
    }),
    settle(async () => {
      const rows = await readRows(fetcher, "/applications");
      return rows === null ? FAILED : ok(rows);
    }),
  ]);
  return { profile, credentials, savedSearches, applications };
}

function mapFetched<T, U>(fact: Fetched<T>, map: (value: T) => U): Fetched<U> {
  return fact.kind === "ok" ? ok(map(fact.value)) : fact;
}

// What the checklist decides from the reads.
export function firstRunFactsOf(reads: AccountReads): FirstRunFacts {
  return {
    profile: mapFetched(reads.profile, (profile) => profile.active),
    ...(reads.profile.kind === "ok" && reads.profile.value.activatedAt !== null
      ? { profileActiveSince: reads.profile.value.activatedAt }
      : {}),
    // Usable means checked with a model chosen (lib/modelKey.ts): a key saved with no model is not a
    // finished step, and the step then says what is left.
    modelKey: mapFetched(reads.credentials, (rows) => modelKeyOf(rows) === "ready"),
    modelKeyNeedsModel: mapFetched(reads.credentials, (rows) => modelKeyOf(rows) === "needs_model"),
    ...(reads.credentials.kind === "ok" ? { modelKeyDetail: modelKeyDetailOf(reads.credentials.value) } : {}),
    savedSearches: mapFetched(reads.savedSearches, (rows) => rows.length),
    applications: mapFetched(reads.applications, (rows) => {
      const withoutResume = rows.find((row) => row.resume_exists !== true && nonEmptyString(row.id));
      const first = rows.find((row) => nonEmptyString(row.id));
      return {
        count: rows.length,
        fromDiscover: rows.filter((row) => row.source_channel === "discover").length,
        withResume: rows.filter((row) => row.resume_exists === true).length,
        resumeTargetId: nonEmptyString((withoutResume ?? first)?.id),
      };
    }),
  };
}

export async function loadFirstRunFacts(fetcher: Fetcher): Promise<FirstRunFacts> {
  return firstRunFactsOf(await readAccount(fetcher));
}

// ── deriving the steps ─────────────────────────────────────────────────────

export type StepId = "profile" | "model_key" | "first_search" | "track_job" | "generate_resume";
export type StepStatus = "done" | "todo" | "unknown";

// Why a step could not be told. "errored": a read it depends on failed, so it can be tried again.
// "cant_tell": every read worked and none holds the answer (a search that was not saved or tracked
// leaves no row), so trying again changes nothing.
export type UnknownCause = "errored" | "cant_tell";

// The real fact behind a done step, when one was returned: a date from the profile row, or words
// built from counts of rows. Null when nothing returned says more than "done".
export type StepFact = { kind: "since"; at: string } | { kind: "text"; text: string };

export interface FirstRunStep {
  id: StepId;
  label: string;
  hint: string;
  // The page where the step is done.
  to: string;
  status: StepStatus;
  // Set on an unknown step only.
  cause: UnknownCause | null;
  // Set on a done step only.
  fact: StepFact | null;
}

// How the checklist is drawn. "expanded": the five steps, because some step is known to be to do.
// "collapsed": one line -- "Setup ready" when all five are done, otherwise the counts -- which opens
// on a click. "hidden": nothing yet, because the reads have not come back.
export type SetupPresentation = "expanded" | "collapsed" | "hidden";

export interface FirstRunView {
  // Whether the card is shown open by default.
  visible: boolean;
  presentation: SetupPresentation;
  // All five are done: known from the reads, or remembered by this browser while they are loading.
  finished: boolean;
  // The reads have not all answered, so nothing about the account is said yet: the collapsed line
  // (kept for an account this browser has seen finished) reads only that it is checking.
  loading: boolean;
  // Counts for the collapsed line; the steps below are what they come from.
  todoCount: number;
  unknownCount: number;
  steps: readonly FirstRunStep[];
  doneCount: number;
  // The first step KNOWN to be todo, or null when there is none. A step that could not be
  // checked is never named as the next one: the card would be telling the person to do
  // something it does not know they have not done.
  nextStep: FirstRunStep | null;
}

function statusOfFlag(fact: Fetched<boolean>): StepStatus {
  if (fact.kind !== "ok") return "unknown";
  return fact.value ? "done" : "todo";
}

// A step that depends on reads that did not all arrive is unknown for one of two reasons.
function causeOf(status: StepStatus, ...reads: readonly Fetched<unknown>[]): UnknownCause | null {
  if (status !== "unknown") return null;
  return reads.some((read) => read.kind === "failed") ? "errored" : "cant_tell";
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

function firstSearchFact(facts: FirstRunFacts, flags: FirstRunFlags): StepFact | null {
  if (facts.savedSearches.kind === "ok" && facts.savedSearches.value > 0) {
    return { kind: "text", text: count(facts.savedSearches.value, "saved search", "saved searches") };
  }
  if (facts.applications.kind === "ok" && facts.applications.value.fromDiscover > 0) {
    return { kind: "text", text: `${facts.applications.value.fromDiscover} tracked from Discover` };
  }
  return flags.searched === true ? { kind: "text", text: "A search finished in this browser" } : null;
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function resumeLink(facts: FirstRunFacts): string {
  if (facts.applications.kind === "ok" && facts.applications.value.resumeTargetId !== null) {
    return `/applications/${encodeURIComponent(facts.applications.value.resumeTargetId)}#${CROSS_NAV_HASH.generate}`;
  }
  return "/applications";
}

// The model step's description line. The step's LABEL is the shared one ("Add a model key", "Choose a
// model": the chat's copy and its tests pin it), so the plain words about what is connected live here,
// in the web's own line. They come from the credentials rows already read, never from a new request.
function listNames(names: readonly string[]): string {
  return names.length <= 2 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function modelHint(facts: FirstRunFacts, awaitingModel: boolean): string {
  const detail = facts.modelKeyDetail;
  const who = detail !== undefined && detail.providers.length > 0 ? listNames(detail.providers) : null;
  if (awaitingModel) {
    return who === null
      ? "Your key is saved and checked, but no model is chosen for it yet. Choose one to use it."
      : `${modelStatusWords("needs_model", who)}. The key is saved and checked; a model is the last step.`;
  }
  if (detail?.reading === "needs_key" && who !== null) {
    return `${modelStatusWords("needs_key", who)}. The saved key isn't confirmed, so it can't be used.`;
  }
  return `${CONNECT_MODEL_WORDS}. Paste your own key. This app never runs on a shared key.`;
}

function modelDoneFact(facts: FirstRunFacts): string {
  const detail = facts.modelKeyDetail;
  if (detail === undefined || detail.reading !== "ready" || detail.providers.length === 0) return "Key saved and checked";
  const names = listNames(detail.providers);
  return `AI model: ${names}, ${detail.providers.length === 1 ? "model" : "models"} chosen`;
}

export function deriveFirstRun(facts: FirstRunFacts, flags: FirstRunFlags): FirstRunView {
  const applications = facts.applications;
  // The key is there; the model is what is missing. Only claimed when both facts were read.
  const awaitingModel =
    facts.modelKey.kind === "ok" &&
    !facts.modelKey.value &&
    facts.modelKeyNeedsModel?.kind === "ok" &&
    facts.modelKeyNeedsModel.value;
  const profile = statusOfFlag(facts.profile);
  const modelKey = statusOfFlag(facts.modelKey);
  const firstSearch = firstSearchStatus(facts, flags);
  const tracked: StepStatus =
    applications.kind === "ok" ? (applications.value.count > 0 ? "done" : "todo") : "unknown";
  const generated: StepStatus =
    applications.kind === "ok" ? (applications.value.withResume > 0 ? "done" : "todo") : "unknown";
  const steps: FirstRunStep[] = [
    {
      id: "profile",
      label: "Add your profile",
      hint: "Import your resume as a profile. Everything else is built from it.",
      to: "/profile",
      status: profile,
      cause: causeOf(profile, facts.profile),
      fact:
        profile === "done"
          ? typeof facts.profileActiveSince === "string" && facts.profileActiveSince !== ""
            ? { kind: "since", at: facts.profileActiveSince }
            : null
          : null,
    },
    {
      id: "model_key",
      label: awaitingModel ? MODEL_CHOICE_LABEL : "Add a model key",
      hint: modelHint(facts, awaitingModel),
      to: MODEL_PROVIDER_PATH,
      status: modelKey,
      cause: causeOf(modelKey, facts.modelKey),
      fact: modelKey === "done" ? { kind: "text", text: modelDoneFact(facts) } : null,
    },
    {
      id: "first_search",
      label: "Run a first search",
      hint: "Search for a role, or leave the filters empty to browse the latest postings.",
      to: "/discover",
      status: firstSearch,
      cause: causeOf(firstSearch, facts.savedSearches, applications),
      fact: firstSearch === "done" ? firstSearchFact(facts, flags) : null,
    },
    {
      id: "track_job",
      label: "Track a job",
      hint: "Track one from a search, or paste a posting on Applications.",
      to: "/applications",
      status: tracked,
      cause: causeOf(tracked, applications),
      fact:
        applications.kind === "ok" && tracked === "done"
          ? { kind: "text", text: `${applications.value.count} tracked` }
          : null,
    },
    {
      id: "generate_resume",
      label: "Generate a resume",
      hint: "Open a tracked job and press Generate resume.",
      to: resumeLink(facts),
      status: generated,
      cause: causeOf(generated, applications),
      fact:
        applications.kind === "ok" && generated === "done"
          ? { kind: "text", text: `${applications.value.withResume} with a resume` }
          : null,
    },
  ];

  const doneCount = steps.filter((step) => step.status === "done").length;
  const todoCount = steps.filter((step) => step.status === "todo").length;
  const unknownCount = steps.filter((step) => step.status === "unknown").length;
  const stillLoading = !factsArrived(facts);
  const nextStep = steps.find((step) => step.status === "todo") ?? null;
  const visible = !flags.dismissed && !stillLoading && nextStep !== null;
  // Finished is a fact about the account, so it is only ever drawn from the reads.
  const finished = !stillLoading && doneCount === steps.length;
  const presentation: SetupPresentation = visible
    ? "expanded"
    : stillLoading && flags.seenFinished !== true
      ? "hidden"
      : "collapsed";
  return { visible, presentation, finished, loading: stillLoading, todoCount, unknownCount, steps, doneCount, nextStep };
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

// An account seen with all five steps done. Remembered per user, per browser, so a later visit keeps
// a place for the one setup line while the four reads are out (a layout hint: it claims nothing).
// Once they have come back the line says what they say, never what was remembered. A browser that
// has lost it (site data cleared, a new browser) loses nothing but that reserved place.
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

// An account the reads now say has a step to do is no longer one that finished. (`StorageLike` has
// no remove, so the flag is overwritten with anything but the set value.)
export function clearCompleted(storage: StorageLike | null, userId: string): void {
  try {
    // Only a flag that is set is overwritten: an account that never finished stores nothing.
    if (storage?.getItem(completedKey(userId)) === FLAG_SET) storage.setItem(completedKey(userId), "0");
  } catch {
    // Nothing useful to do: the line only keeps a place for itself while the reads are out.
  }
}

// ── the checklist as one small machine ─────────────────────────────────────

// Everything useFirstRun.ts keeps, and every decision it makes, so that the hook is only
// wiring: what is read from storage and under which person's key, what is written when the
// card is hidden or the account is seen finished, and what the card then shows. Each is a plain
// function over an injected storage. The reads themselves are not here: Today makes them once
// (readAccount) because it needs more of them than the checklist does, and hands them in.
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
  modelKeyNeedsModel: { kind: "loading" },
  savedSearches: { kind: "loading" },
  applications: { kind: "loading" },
};

// Whether every read the checklist depends on has answered, well or badly.
export function factsArrived(facts: FirstRunFacts): boolean {
  return [facts.profile, facts.modelKey, facts.savedSearches, facts.applications].every((fact) => fact.kind !== "loading");
}

export function initialFirstRunState(storage: StorageLike | null, userId: string): FirstRunState {
  return {
    dismissed: readDismissed(storage, userId),
    completed: readCompleted(storage, userId),
    facts: STILL_LOADING,
    searched: null,
  };
}

// What to take from a load that has arrived. The browser's first-search flag is read now,
// not at mount: the Discover page sets it, and a fresh visit to Today runs this again. An
// account with all five steps done is written down as completed, from this answer only; an
// answer that says otherwise (a step to do again, or one that could not be read) clears the
// in-memory flag for this visit, so the line is never "ready" against the reads.
export function onFirstRunFactsLoaded(
  facts: FirstRunFacts,
  storage: StorageLike | null,
  userId: string,
): Pick<FirstRunState, "facts" | "searched" | "completed"> {
  const searched = readSearched(storage, userId);
  const view = deriveFirstRun(facts, { dismissed: false, searched });
  const finished = view.doneCount === view.steps.length;
  if (finished) writeCompleted(storage, userId);
  // A step that is definitely to do again ends the memory; one that merely could not be read does
  // not (a network blip says nothing about whether the account finished).
  else if (view.todoCount > 0) clearCompleted(storage, userId);
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
    dismissed: state.dismissed,
    seenFinished: state.completed,
    searched: state.searched,
  });
}
