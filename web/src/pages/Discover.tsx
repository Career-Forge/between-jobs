import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth";
import { DiscoverView, type SearchView, type SetupView } from "../components/discover/DiscoverView";
import type { StateLink } from "../components/paper/StateViews";
import { apiFetch } from "../lib/api";
import { trackRequestBody } from "../lib/discover";
import {
  EMPTY_REQUEST,
  discoverQuery,
  resultsAnnouncement,
  resultsFactOf,
  savedSearchBody,
  type DiscoverRequest,
} from "../lib/discoverFacts";
import type { DiscoverResponse, JobCard } from "../lib/discoverTypes";
import { browserStorage, markSearched } from "../lib/firstRun";
import { MODEL_CHOICE_LABEL, modelKeyOf, type ModelKeyReading } from "../lib/modelKey";
import { nextStep, type NextStepInput } from "../lib/nextStep";
import type { DiscoverFacts, DiscoverPhase } from "../lib/nextStep/pages/discover";
import { friendlyApiMessage } from "../lib/rateLimitMessage";
import { createSavedSearch } from "../lib/savedSearches";
import { searchKeyCount, setupChecks } from "../lib/setupReads";
import { MODEL_PROVIDER_PATH, SETTINGS_PATH } from "../lib/settingsPaths";
import { type SetupNotice, failureOf } from "../lib/setupRequired";
import { clockTime } from "../lib/todayFeed";
import { useBandFocus } from "../lib/useBandFocus";
import { useDiscoverHold } from "../lib/useDiscoverHold";
import { useIsWide } from "../lib/useMediaQuery";
import { useSavedSearches } from "../lib/useSavedSearches";
import { useSetupReads } from "../lib/useSetupReads";

// Discover -- the full native search pipeline: the 9 live-search providers and the job registry,
// merged, de-duplicated and filtered, each live link checked, and the best of them scored against the
// profile by the person's own model, all in one request. "Track" reuses the same manual-paste job
// ingestion Applications and Telegram already use -- one write path, several ways to reach it.
//
// THE PAGE'S ONE CYAN BUTTON is the next-step band's (lib/nextStep/pages/discover.ts): "Search" when
// the page is ready and nothing has been searched, "Search again" when the scoring fell back, "Clear
// filters" when nothing matched. Before the first search the band's Search is the only one on the page
// (the form draws none until a search has run, and Enter in a field still presses the band's); after
// that the form's own "Search again" is outlined, and so is every row's Track.
//
// WHAT RUNS WHEN. Opening the page runs NO search: a search is a full discovery (provider calls,
// liveness probes, a model call) and spends one slot of the per-user "discover" limit, so a search on
// mount would use the budget up on page visits (two a visit under StrictMode in development). The page
// reads GET /profile/current and GET /credentials (the setup the search needs) and GET /saved-searches
// when it opens, so the band knows what is missing before any request that would need it is made. A
// search is made only by the form (Enter, its Search, or the band's), one at a time, and never while
// the setup reads show it missing (a server answer of SETUP_REQUIRED does not stop the next press: the
// server is asked again). A test pins this (Discover.test.tsx).
//
// WHAT IS KEPT. The page unmounts when the person opens a job or goes to another page, so the last finished
// answer is held in memory outside it (lib/discoverHold.ts, handed down by lib/useDiscoverHold.tsx) and read
// once when the page opens again. A held answer comes back as it was: the same rows, the form filled with the
// filters that produced it, and the postings already tracked from it. Showing it makes no request, and it is
// not a new search, so it does not count as one for the first-run checklist. It says when it is from and that
// nothing was refreshed (DiscoverView.tsx), and pressing Search again replaces it like any other search.
// What is held is the last answer that came back with rows: a search that is running, that failed or that
// asked for setup leaves it where it is, so a failed "Search again" does not take the earlier list away,
// while an answer with no rows and "Clear filters" empty it. It lives in memory only (a reload starts
// empty), is dropped after two hours counted from when the answer arrived, and belongs to one signed-in
// person. A posting whose Track request is still out when the person leaves is not added to the held answer
// (the page that asked is gone), so it offers Track again on return; pressing it hands back the same
// application, because creating one is idempotent per person and posting.
//
// This file is the reading and the requests; what is drawn for each state is
// components/discover/DiscoverView.tsx, the row words are lib/discoverRow.ts.

type State =
  | { kind: "idle" }
  | { kind: "loading"; startedAt: number; request: DiscoverRequest }
  | { kind: "setup"; notice: SetupNotice; request: DiscoverRequest }
  | { kind: "error"; message: string; at: string; request: DiscoverRequest }
  // `at` is when the answer arrived (milliseconds since the epoch); `restored` marks an answer brought back
  // from the hold on opening the page, as opposed to one this visit searched for.
  | { kind: "ready"; result: DiscoverResponse; request: DiscoverRequest; at: number; restored: boolean };

const SEARCH_INPUT_SELECTOR = ".bj-disc-form input[type='text']";

function phaseOf(state: State): DiscoverPhase {
  if (state.kind === "loading") return "searching";
  if (state.kind === "ready") return "results";
  if (state.kind === "setup") return "setup";
  return "idle";
}

// What is missing, as links to where each is fixed, from what was read and what the server said.
function missingFrom(profileActive: boolean | null, key: ModelKeyReading | null, notice: SetupNotice | null): StateLink[] {
  const missing: StateLink[] = [];
  if (profileActive === false) missing.push({ label: "Add your profile", to: "/profile" });
  if (key !== null && key !== "ready") missing.push({ label: key === "needs_model" ? MODEL_CHOICE_LABEL : "Add a model key", to: MODEL_PROVIDER_PATH });
  if (missing.length === 0 && notice !== null) {
    // The server's own link, when it named one the app routes; otherwise Settings, where setup is done.
    missing.push(notice.linkTo !== null && notice.linkLabel !== null ? { label: notice.linkLabel, to: notice.linkTo } : { label: "Open Settings", to: SETTINGS_PATH });
  }
  return missing;
}

export default function Discover() {
  const { session } = useAuth();
  const userId = session?.user.id ?? null;
  const setup = useSetupReads();
  const saved = useSavedSearches();
  const [now] = useState(() => new Date());
  // From 1280px a finished search is a list beside the chosen row in full; the same width at which Today
  // draws its right column.
  const wide = useIsWide();
  const hold = useDiscoverHold();
  // The held answer, read once when the page opens: in a state initialiser, so no render reads it again, and
  // the three states below start from this one read. Reading takes nothing out of the hold, so a development
  // build that runs the initialiser twice reads the same answer twice.
  const [restore] = useState(() => hold.read());
  const [values, setValues] = useState<DiscoverRequest>(() => restore?.request ?? EMPTY_REQUEST);
  const [state, setState] = useState<State>(() =>
    restore === null ? { kind: "idle" } : { kind: "ready", result: restore.result, request: restore.request, at: restore.at, restored: true },
  );
  const [tracking, setTracking] = useState<ReadonlySet<string>>(new Set());
  const [tracked, setTracked] = useState<Readonly<Record<string, string>>>(() => restore?.tracked ?? {});
  const [trackErrors, setTrackErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const searchInFlight = useRef(false);
  const trackInFlight = useRef(new Set<string>());
  // The setup reads are still out (on opening, and again after Try again on a failed check).
  const checkingSetup = setup.reads.profile.kind === "loading" || setup.reads.credentials.kind === "loading";
  // Focus is held on the band until the search and the setup reads have answered.
  const bandFocus = useBandFocus(state.kind !== "loading" && !checkingSetup);

  // ── what setup says ──────────────────────────────────────────────────────

  const credentials = setup.reads.credentials;
  const key: ModelKeyReading | null = credentials.kind === "ok" ? modelKeyOf(credentials.value) : null;
  const searchProviders = credentials.kind === "ok" ? searchKeyCount(credentials.value) : null;
  const profileRead = setup.reads.profile;
  const profileActive = profileRead.kind === "ok" ? profileRead.value.active : null;
  const serverNotice = state.kind === "setup" ? state.notice : null;
  // The server said the profile is what is missing: believe it over a read that said it was there.
  const serverSaysNoProfile = serverNotice !== null && serverNotice.linkTo === "/profile";
  const knownMissing = missingFrom(serverSaysNoProfile ? false : profileActive, key, serverNotice);
  // Setup is shown instead of the search's body when the server just said it is missing, or when the
  // reads say so before anything has been searched. A search is never made in either case.
  const readMissing = profileActive === false || (key !== null && key !== "ready");
  const setupView: SetupView | null =
    knownMissing.length > 0 && (state.kind === "setup" || (state.kind === "idle" && readMissing))
      ? {
          message: serverNotice?.message ?? "Discover scores jobs against your profile with your model.",
          missing: knownMissing as [StateLink, ...StateLink[]],
          blocksSearch: readMissing,
        }
      : null;
  // Only what the reads show stops a search. When just the server said setup is missing, a second
  // press asks it again: the person may have fixed it in another tab, and the server is the authority.
  const searchBlocked = setupView !== null && setupView.blocksSearch === true;

  // ── what the band is given ───────────────────────────────────────────────

  const facts: DiscoverFacts = useMemo(
    () => ({
      phase: phaseOf(state),
      key,
      searchProviders,
      results: state.kind === "ready" ? resultsFactOf(state.result, state.request) : null,
    }),
    [state, key, searchProviders],
  );

  const outcome = useMemo(() => {
    const checks = setupChecks(setup.reads, setup.at);
    const profile = serverSaysNoProfile && checks.profile?.state === "ready" ? { state: "ready" as const, value: { active: false, importWaiting: false } } : checks.profile;
    const input: NextStepInput = {
      page: "discover",
      signedIn: true,
      profile,
      credentials: checks.credentials,
      pageFailure: state.kind === "error" ? { what: "Couldn't finish the search", call: "GET /discover", at: state.at, intent: "retry" } : null,
      facts,
    };
    return nextStep(input);
  }, [setup.reads, setup.at, serverSaysNoProfile, state, facts]);

  // ── the requests ─────────────────────────────────────────────────────────

  const search = useCallback(
    async (request: DiscoverRequest) => {
      // One search at a time: each spends a slot of the person's hourly limit.
      if (searchInFlight.current) return;
      searchInFlight.current = true;
      setState({ kind: "loading", startedAt: Date.now(), request });
      setAnnouncement("");
      try {
        const result = await apiFetch<DiscoverResponse>(`/discover?${discoverQuery(request)}`);
        setState({ kind: "ready", result, request, at: Date.now(), restored: false });
        setAnnouncement(resultsAnnouncement(resultsFactOf(result, request)));
        // A search leaves no row, so the first-run checklist (Today) remembers that this person has
        // run one, per user, in this browser (see lib/firstRun.ts).
        if (userId !== null) markSearched(browserStorage(), userId);
      } catch (e) {
        const failure = failureOf(e, "Failed to search");
        setState(
          failure.kind === "setup"
            ? { kind: "setup", notice: failure.notice, request }
            : { kind: "error", message: failure.message, at: clockTime(new Date()), request },
        );
      } finally {
        searchInFlight.current = false;
      }
    },
    [userId],
  );

  // Opening the page must NOT search (see the header). The only effects here read the account, in the
  // hooks above, and keep the hold below; none of them calls `search`.

  // Keeps the hold true to the page (see WHAT IS KEPT in the header). A finished answer with rows is held with
  // what was tracked from it, an answer with no rows empties the hold, and anything else (a search running,
  // a failure, a setup answer, the idle page) leaves the held answer alone, except that a row tracked while
  // one of those is on show still belongs to the answer it came from. Every write keeps the time the answer
  // arrived (`state.at` on this page, the held stamp in the branch that rewrites the held entry), so tracking
  // a row, or showing an answer again, never makes it younger. "No rows" is counted by resultsFactOf, the
  // reading the next-step band's "No results" step uses, so the hold and the band cannot disagree about it.
  useEffect(() => {
    if (state.kind !== "ready") {
      const held = hold.read();
      if (held !== null && held.tracked !== tracked) hold.write({ ...held, tracked });
      return;
    }
    const rows = resultsFactOf(state.result, state.request);
    if (rows.scored + rows.more === 0) {
      hold.clear();
      return;
    }
    hold.write({ result: state.result, request: state.request, tracked, at: state.at });
  }, [hold, state, tracked]);

  function runSearch() {
    if (searchBlocked) return;
    // The band's button is where focus is when it asks; that button is replaced by "Searching...", so
    // focus is handed to the band's own sentence rather than dropped.
    bandFocus.armIfBandFocused();
    void search(values);
  }

  async function saveCurrentSearch() {
    if (saving) return;
    setSaving(true);
    setCreateError(null);
    const result = await createSavedSearch(apiFetch, savedSearchBody(values));
    if (result.ok) {
      saved.add(result.search);
      // The list takes the new search only once it has been read; saying "saved" without that would
      // point at a list that does not show it.
      setAnnouncement(saved.state.kind === "ready" ? "Search saved." : "Search saved, but the saved searches list isn't showing it yet.");
    } else {
      setCreateError(result.message);
    }
    setSaving(false);
  }

  async function track(job: JobCard) {
    if (trackInFlight.current.has(job.apply_url)) return;
    trackInFlight.current.add(job.apply_url);
    setTracking(new Set(trackInFlight.current));
    setTrackErrors((current) => {
      const { [job.apply_url]: _cleared, ...rest } = current;
      return rest;
    });
    try {
      const application = await apiFetch<{ id: string }>("/discover/track", {
        method: "POST",
        body: JSON.stringify(trackRequestBody(job)),
      });
      setTracked((current) => ({ ...current, [job.apply_url]: application.id }));
      setAnnouncement(`Tracked ${job.title}. Open workspace is now available.`);
    } catch (e) {
      setTrackErrors((current) => ({ ...current, [job.apply_url]: friendlyApiMessage(e, "Failed to track") }));
    } finally {
      trackInFlight.current.delete(job.apply_url);
      setTracking(new Set(trackInFlight.current));
    }
  }

  function onBandAction(intent: string) {
    if (intent === "retry") {
      if (state.kind === "error") {
        bandFocus.arm();
        void search(state.request);
      }
      return;
    }
    if (intent === "retry-setup") {
      bandFocus.arm();
      setup.retry();
      return;
    }
    if (intent === "clear-filters") {
      // Clearing the filters starts the page over, so nothing is held to come back to. The answer that offers
      // this button has no rows and has already emptied the hold (the effect above), so this line only says
      // the same thing here, where the page is reset, in case the button is ever offered for another answer.
      hold.clear();
      setValues(EMPTY_REQUEST);
      setState({ kind: "idle" });
      setAnnouncement("Filters cleared.");
      // The band's button is replaced by the next step; the search box is where a person goes next.
      window.setTimeout(() => document.querySelector<HTMLInputElement>(SEARCH_INPUT_SELECTOR)?.focus(), 0);
    }
  }

  const searchView: SearchView =
    state.kind === "loading"
      ? { kind: "loading", startedAt: state.startedAt }
      : state.kind === "error"
        ? { kind: "error", message: state.message }
        : state.kind === "ready"
          ? { kind: "ready", result: state.result }
          : { kind: "idle" };

  return (
    <DiscoverView
      now={now}
      outcome={outcome}
      onBandAction={onBandAction}
      announcement={announcement}
      values={values}
      onValuesChange={setValues}
      onSearch={runSearch}
      onSave={() => void saveCurrentSearch()}
      saving={saving}
      search={searchView}
      restored={state.kind === "ready" && state.restored ? { at: clockTime(new Date(state.at)) } : undefined}
      setup={setupView}
      checkingSetup={checkingSetup}
      tracking={tracking}
      tracked={tracked}
      trackErrors={trackErrors}
      onTrack={(job) => void track(job)}
      saved={{ state: saved.state, actions: { toggle: saved.toggle, remove: saved.remove, retry: saved.retry }, createError }}
      wide={wide}
    />
  );
}
