import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FirstRunChecklist } from "../components/FirstRunChecklist";
import { NEXT_STEP_FOCUS_ID } from "../components/paper/NextStepBand";
import type { BusyKind } from "../components/today/ItemParts";
import { ACTIVITY_HEADING_ID, TodayView, type AccountNotes, type FeedState, type TodayConfirmation } from "../components/today/TodayView";
import { apiFetch } from "../lib/api";
import { ACCOUNT_STILL_LOADING, firstRunFactsOf, hasOpenRouterKey, readAccount, type AccountReads } from "../lib/firstRun";
import { nextStepWithRow, type NextStepInput, type SetupCheck } from "../lib/nextStep";
import { PROPOSAL_STEPS, parseTodayIntent, todayLit, workspacePath, type TodayRowId } from "../lib/nextStep/pages/today";
import { gmailAccess } from "../lib/gmailAccess";
import { friendlyApiMessage } from "../lib/rateLimitMessage";
import { failureOf, type Failure } from "../lib/setupRequired";
import {
  applicationNames,
  backgroundView,
  clockTime,
  fitReadOf,
  fitReadTargets,
  isPending,
  jobLabel,
  searchesRead,
  todayFacts,
  trackRequestBody,
  type FitRead,
  type RowContext,
  type TodayItem,
} from "../lib/todayFeed";
import { useCapabilities } from "../lib/useCapabilities";
import { useMediaQuery } from "../lib/useMediaQuery";

// Today, the page a signed-in person lands on. It says one thing to do next: the next-step band
// carries the most urgent item itself (lib/nextStep/pages/today.ts decides which), and that item is
// then left out of the list below it. This file is the reading and the requests; what is drawn for
// each state is components/today/TodayView.tsx, and what each line says is lib/todayFeed.ts.
//
// Requests. GET /today is the page's own data: when it fails the band says so (row F1) and offers
// Try again. GET /profile/current, /credentials, /saved-searches and /applications are read once
// (readAccount) and shared by the band's setup rows, the setup checklist, the "In the background"
// trail and the job names in sentences. A held-back resume asks GET /applications/{id}/prepare-result
// once for the fit check's score. Actions are the ones Today has always had -- dismiss, accept or
// dismiss a status proposal, track a high fit job -- plus "Generate anyway" for a held-back resume,
// which is the workspace's own generation with the fit gate bypassed. A failed action is shown under
// the item it was for and leaves the rest of the page as it was.

// The widest the right margin fits: the same width at which the shell stops capping the column.
const WIDE_QUERY = "(min-width: 1280px)";

type FeedLoad = { kind: "loading" } | { kind: "failed"; message: string; at: string } | { kind: "ready"; items: TodayItem[] };

type ItemAction = BusyKind;

interface Account {
  reads: AccountReads;
  /** The client time the reads came back, for the failure line of a read that failed. */
  at: string;
  /** Which request of the account these reads answer; behind the current one, they are about to be replaced. */
  attempt: number;
}

// Words for a screen reader as a request starts and as it ends well. A failure is announced by its
// own alert under the item, so it needs none here.
const STARTED: Record<ItemAction, string> = {
  dismiss: "Dismissing",
  accept: "Moving the application",
  track: "Tracking the job",
  "generate-anyway": "Generating your resume",
};

// Where keyboard focus goes when the control that had it is gone: the band's step (its button, or
// its sentence when nothing is lit), or the list's heading for a row.
type FocusTarget = "band" | "list";

function checkOf<T, V>(
  read: AccountReads["profile"] | AccountReads["credentials"],
  call: string,
  at: string,
  value: (ok: T) => V,
): SetupCheck<V> {
  if (read.kind === "loading") return { state: "loading" };
  if (read.kind === "failed") return { state: "failed", call, at };
  return { state: "ready", value: value(read.value as T) };
}

const LIT_ROWS: readonly string[] = ["T1", "T2", "T3", "T4"];

export default function Today() {
  const [feed, setFeed] = useState<FeedLoad>({ kind: "loading" });
  const [account, setAccount] = useState<Account>({ reads: ACCOUNT_STILL_LOADING, at: "", attempt: 0 });
  const [accountAttempt, setAccountAttempt] = useState(0);
  const [busy, setBusy] = useState<Record<string, BusyKind | undefined>>({});
  const [problems, setProblems] = useState<Record<string, Failure | undefined>>({});
  const [tracked, setTracked] = useState<Record<string, string>>({});
  const [announcement, setAnnouncement] = useState("");
  const [confirmation, setConfirmation] = useState<TodayConfirmation | null>(null);
  const [refocus, setRefocus] = useState<FocusTarget | null>(null);
  const [fits, setFits] = useState<Record<string, FitRead | undefined>>({});
  // The applications whose stored fit result this feed's held-back cards ask for: chosen once per
  // load of the feed (a capped few, newest first), so what is waited for never changes while it is.
  const [fitTargets, setFitTargets] = useState<readonly string[]>([]);
  const [now] = useState(() => new Date());
  const wide = useMediaQuery(WIDE_QUERY);
  const inFlight = useRef(new Set<string>());
  const lastAction = useRef(new Map<string, { action: ItemAction; item: TodayItem }>());
  const askedFit = useRef(new Set<string>());

  const load = useCallback(async () => {
    setFeed({ kind: "loading" });
    try {
      const items = await apiFetch<TodayItem[]>("/today");
      setFeed({ kind: "ready", items });
      setFitTargets(fitReadTargets(items));
    } catch (e) {
      setFeed({ kind: "failed", message: e instanceof Error ? e.message : "Failed to load", at: clockTime(new Date()) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    setAccount((current) => (current.reads === ACCOUNT_STILL_LOADING ? current : { ...current, reads: ACCOUNT_STILL_LOADING, at: "" }));
    void readAccount(apiFetch).then((reads) => {
      if (!cancelled) setAccount({ reads, at: clockTime(new Date()), attempt: accountAttempt });
    });
    return () => {
      cancelled = true;
    };
  }, [accountAttempt]);

  const retryAccount = useCallback(() => setAccountAttempt((attempt) => attempt + 1), []);

  // A held-back resume's chip carries the fit check's score, and whether the card is still true
  // (a resume may exist by now), which only the stored result has. One read per targeted
  // application, at most FIT_READ_CAP for each load of the feed, and never a retry: a failed read
  // just leaves the card saying only what it is.
  useEffect(() => {
    for (const applicationId of fitTargets) {
      if (askedFit.current.has(applicationId)) continue;
      askedFit.current.add(applicationId);
      apiFetch<unknown>(`/applications/${encodeURIComponent(applicationId)}/prepare-result`)
        .then((body) => setFits((current) => ({ ...current, [applicationId]: fitReadOf(body) })))
        .catch(() => setFits((current) => ({ ...current, [applicationId]: { state: "unavailable" } })));
    }
  }, [fitTargets]);

  // What is known about each targeted application: a read still out is "loading" from the render
  // that chose it, so the band never lights a card a moment before its read can take it back.
  const fitReads = useMemo(() => {
    const reads: Record<string, FitRead | undefined> = { ...fits };
    for (const applicationId of fitTargets) reads[applicationId] ??= { state: "loading" };
    return reads;
  }, [fits, fitTargets]);

  // ── what the band is given ───────────────────────────────────────────────

  const searches = useMemo(() => searchesRead(account.reads.savedSearches), [account.reads.savedSearches]);
  const facts = useMemo(
    () =>
      todayFacts({
        items: feed.kind === "ready" ? feed.items : null,
        applications: account.reads.applications,
        searches,
        tracked,
        fits: fitReads,
      }),
    [feed, account.reads.applications, searches, tracked, fitReads],
  );

  const input: NextStepInput = useMemo(
    () => ({
      page: "today",
      signedIn: true,
      profile: checkOf(account.reads.profile, "GET /profile/current", account.at, (profile: { active: boolean }) => ({
        active: profile.active,
        // A waiting import is known only in the session that made it, and this is not that page.
        importWaiting: false,
      })),
      credentials: checkOf(account.reads.credentials, "GET /credentials", account.at, (rows: readonly Record<string, unknown>[]) => ({
        openrouter: hasOpenRouterKey(rows),
      })),
      pageFailure:
        feed.kind === "failed" ? { what: "Couldn't load Today", call: "GET /today", at: feed.at, intent: "retry" } : null,
      facts,
    }),
    [account, feed, facts],
  );
  const { outcome, rowId } = useMemo(() => nextStepWithRow(input), [input]);

  // The item the band holds, lifted out of the list: the same choice the rule table made.
  const lit = useMemo(() => {
    if (rowId === null || !LIT_ROWS.includes(rowId) || facts.feed.state !== "ready") return null;
    const choice = todayLit(facts.feed.items);
    if (choice === null || feed.kind !== "ready") return null;
    const item = feed.items.find((candidate) => candidate.id === choice.item.id);
    return item === undefined ? null : { row: choice.row as TodayRowId, item };
  }, [rowId, facts, feed]);

  const context: RowContext = useMemo(
    () => ({
      now,
      names: account.reads.applications.kind === "ok" ? applicationNames(account.reads.applications.value) : new Map(),
      searchLabels: new Map(searches.state === "ready" ? searches.searches.map((search) => [search.id, search.label] as const) : []),
      fits: fitReads,
    }),
    [now, account.reads.applications, searches, fitReads],
  );

  const firstRunFacts = useMemo(() => firstRunFactsOf(account.reads), [account.reads]);
  // Whether Gmail is open to this person is the server's answer (GET /capabilities, asked once by
  // the shell); the trail claims nothing about Gmail until it is a definite yes.
  const gmail = gmailAccess(useCapabilities());
  const background = useMemo(() => backgroundView(searches, account.reads.credentials, gmail), [searches, account.reads.credentials, gmail]);

  const profile = account.reads.profile;
  const notes: AccountNotes = {
    setupFailed: profile.kind === "failed" || account.reads.credentials.kind === "failed",
    applicationsFailed: account.reads.applications.kind === "failed",
  };

  // ── the actions ──────────────────────────────────────────────────────────

  function removeItem(itemId: string) {
    setFeed((current) => (current.kind === "ready" ? { ...current, items: current.items.filter((item) => item.id !== itemId) } : current));
  }

  function setProblem(itemId: string, problem: Failure | undefined) {
    setProblems((current) => ({ ...current, [itemId]: problem }));
  }

  // What was done, in the words a screen reader hears, once the change is on screen.
  function finished(words: string, from: FocusTarget) {
    setAnnouncement(words);
    setRefocus(from);
  }

  // Each action is the request Today has always made for it. Both proposal decisions retire the
  // item on the server too, so removing it here on success mirrors a plain dismiss. A failure is
  // kept under the item it was for.
  async function perform(action: ItemAction, item: TodayItem) {
    if (inFlight.current.has(item.id)) return;
    inFlight.current.add(item.id);
    lastAction.current.set(item.id, { action, item });
    // Where focus returns to if the control that had it goes: the band when the item was its own,
    // the list's heading when it was a row.
    const from: FocusTarget = lit?.item.id === item.id ? "band" : "list";
    setBusy((current) => ({ ...current, [item.id]: action }));
    setProblem(item.id, undefined);
    setConfirmation(null);
    setAnnouncement(`${STARTED[action]}.`);
    try {
      switch (action) {
        case "dismiss":
          // Only a pending proposal has a proposal to resolve; the server refuses the proposal
          // route (409) for one that was already decided, so that item is retired like any other.
          await apiFetch(isPending(item.status_proposal) ? `/today/${item.id}/dismiss-proposal` : `/today/${item.id}/dismiss`, { method: "POST" });
          removeItem(item.id);
          finished("Dismissed.", from);
          break;
        case "accept":
          await apiFetch(`/today/${item.id}/accept-proposal`, { method: "POST" });
          removeItem(item.id);
          finished(`Moved to ${PROPOSAL_STEPS[item.status_proposal?.proposed_type ?? ""]?.stage ?? "the new stage"}.`, from);
          break;
        case "track": {
          const match = item.job_match;
          if (match === null) break;
          // Reuses `POST /discover/track` exactly as Discover does: same request shape, same
          // endpoint, no new write path for a job surfaced through the feed.
          const application = await apiFetch<{ id: string }>("/discover/track", {
            method: "POST",
            body: JSON.stringify(trackRequestBody(match)),
          });
          setTracked((current) => ({ ...current, [match.apply_url]: application.id }));
          finished("Tracked.", from);
          break;
        }
        case "generate-anyway": {
          const applicationId = item.application_id;
          if (applicationId === null || applicationId === undefined) break;
          const result = await apiFetch<{ resume?: unknown }>(`/applications/${encodeURIComponent(applicationId)}/prepare`, {
            method: "POST",
            body: JSON.stringify({ idempotency_key: crypto.randomUUID(), force_generate: true, generate_cover_letter: false }),
          });
          if (result.resume === null || result.resume === undefined) {
            setProblem(item.id, { kind: "error", message: "No resume was produced. Open the workspace to see why." });
            setAnnouncement("");
            break;
          }
          // The draft exists now, so the "not generated" card is out of date: retire it. If that
          // request fails the card may come back on a later load, but by then the application's own
          // stored result has a resume, which is what the card is read against ("moved on"), so it is
          // not shown as held back. Either way the person is told what happened and where to see it.
          setFits((current) => ({ ...current, [applicationId]: { state: "moved_on" } }));
          await apiFetch(`/today/${item.id}/dismiss`, { method: "POST" }).catch(() => undefined);
          removeItem(item.id);
          const job = jobLabel(context.names.get(applicationId));
          setConfirmation({
            text: job === null ? "Your resume was generated." : `Your resume for ${job} was generated.`,
            link: { label: "Open workspace", to: workspacePath(applicationId) },
          });
          finished(job === null ? "Your resume was generated." : `Your resume for ${job} was generated.`, from);
          break;
        }
      }
    } catch (e) {
      setProblem(item.id, problemOf(action, e));
      setAnnouncement("");
    } finally {
      inFlight.current.delete(item.id);
      setBusy((current) => ({ ...current, [item.id]: undefined }));
    }
  }

  function onBandAction(intent: string) {
    // A retry replaces the band with its loading state, so the button that was pressed goes: focus
    // comes back to the band once the answer is in.
    if (intent === "retry") {
      setRefocus("band");
      void load();
      return;
    }
    if (intent === "retry-setup") {
      setRefocus("band");
      retryAccount();
      // When the whole API was down the feed failed too, and the body has no Retry of its own (the
      // band holds the only one), so a single press reads both.
      if (feed.kind === "failed") void load();
      return;
    }
    const parsed = parseTodayIntent(intent);
    if (parsed === null || feed.kind !== "ready") return;
    const item = feed.items.find((candidate) => candidate.id === parsed.itemId);
    if (item !== undefined) void perform(parsed.name === "accept" ? "accept" : parsed.name === "track" ? "track" : "generate-anyway", item);
  }

  function onRetryProblem(itemId: string) {
    const last = lastAction.current.get(itemId);
    if (last !== undefined) void perform(last.action, last.item);
  }

  // Moves focus to where the person's place went. It waits until the band and the reads have
  // settled (a retry passes through "Loading..."), and leaves focus alone when it is anywhere real.
  const settled =
    outcome.status !== "loading" &&
    feed.kind !== "loading" &&
    account.attempt === accountAttempt &&
    Object.values(account.reads).every((read) => read.kind !== "loading");
  useEffect(() => {
    if (refocus === null || !settled) return;
    setRefocus(null);
    const active = document.activeElement;
    if (active !== null && active !== document.body) return;
    const target = (refocus === "list" ? document.getElementById(ACTIVITY_HEADING_ID) : null) ?? document.getElementById(NEXT_STEP_FOCUS_ID);
    target?.focus();
  });

  const feedState: FeedState =
    feed.kind === "loading" ? { state: "loading" } : feed.kind === "failed" ? { state: "failed", message: feed.message } : { state: "ready", items: feed.items };

  return (
    <TodayView
      now={now}
      wide={wide}
      outcome={outcome}
      litRow={lit?.row ?? null}
      litItem={lit?.item ?? null}
      feed={feedState}
      context={context}
      tracked={tracked}
      busy={busy}
      problems={problems}
      background={background}
      account={notes}
      announcement={announcement}
      confirmation={confirmation}
      checklist={<FirstRunChecklist facts={firstRunFacts} onRetry={retryAccount} />}
      onBandAction={onBandAction}
      onItemAction={(action, item) => void perform(action, item)}
      onRetryProblem={onRetryProblem}
      onRetryAccount={retryAccount}
    />
  );
}

// What a failed action says, in the words each request has always used: dismiss and accept show the
// error's own message, track the friendly one (a rate limit says when to try again), and generating
// keeps a missing profile or key as a setup notice with its link.
function problemOf(action: ItemAction, error: unknown): Failure {
  switch (action) {
    case "dismiss":
      return { kind: "error", message: error instanceof Error ? error.message : "Failed to dismiss" };
    case "accept":
      return { kind: "error", message: error instanceof Error ? error.message : "Failed to accept" };
    case "track":
      return { kind: "error", message: friendlyApiMessage(error, "Failed to track") };
    case "generate-anyway":
      return failureOf(error, "Failed to generate");
  }
}
