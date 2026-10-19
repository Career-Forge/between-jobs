import { useCallback, useMemo, useRef, useState } from "react";
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
import { useSavedSearches } from "../lib/useSavedSearches";
import { useSetupReads } from "../lib/useSetupReads";

// Discover -- the full native search pipeline: the 9 live-search providers and the job registry,
// merged, de-duplicated and filtered, each live link checked, and the best of them scored against the
// profile by the person's own model, all in one request. "Track" reuses the same manual-paste job
// ingestion Applications and Telegram already use -- one write path, several ways to reach it.
//
// THE PAGE'S ONE CYAN BUTTON is the next-step band's (lib/nextStep/pages/discover.ts): "Search" when
// the page is ready and nothing has been searched, "Search again" when the scoring fell back, "Clear
// filters" when nothing matched. The form's own Search and every row's Track are outlined.
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
// This file is the reading and the requests; what is drawn for each state is
// components/discover/DiscoverView.tsx, the row words are lib/discoverRow.ts.

type State =
  | { kind: "idle" }
  | { kind: "loading"; startedAt: number; request: DiscoverRequest }
  | { kind: "setup"; notice: SetupNotice; request: DiscoverRequest }
  | { kind: "error"; message: string; at: string; request: DiscoverRequest }
  | { kind: "ready"; result: DiscoverResponse; request: DiscoverRequest };

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
  const [values, setValues] = useState<DiscoverRequest>(EMPTY_REQUEST);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [tracking, setTracking] = useState<ReadonlySet<string>>(new Set());
  const [tracked, setTracked] = useState<Record<string, string>>({});
  const [trackErrors, setTrackErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const searchInFlight = useRef(false);
  const trackInFlight = useRef(new Set<string>());
  const bandFocus = useBandFocus(state.kind !== "loading");

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
  const checkingSetup = profileRead.kind === "loading" || credentials.kind === "loading";

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
    const profile = serverSaysNoProfile && checks.profile.state === "ready" ? { state: "ready" as const, value: { active: false, importWaiting: false } } : checks.profile;
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
        setState({ kind: "ready", result, request });
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
  // hooks above; none of them calls `search`.

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
      setup.retry();
      return;
    }
    if (intent === "clear-filters") {
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
      setup={setupView}
      checkingSetup={checkingSetup}
      tracking={tracking}
      tracked={tracked}
      trackErrors={trackErrors}
      onTrack={(job) => void track(job)}
      saved={{ state: saved.state, actions: { toggle: saved.toggle, remove: saved.remove, retry: saved.retry }, createError }}
    />
  );
}
