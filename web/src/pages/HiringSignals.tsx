import { useMemo, useState, useSyncExternalStore } from "react";
import { HiringSignalsPageView } from "../components/HiringSignalsPageView";
import { HiringSignalsTab } from "../components/HiringSignalsTab";
import { apiFetch } from "../lib/api";
import { hiringFactsOf, pageFailureOf, retryableSearchFailure } from "../lib/hiringSignalsPage";
import { HiringTabModel } from "../lib/hiringSignalsTabModel";
import { nextStep, type NextStepInput } from "../lib/nextStep";
import { searchKeyCount, setupChecks } from "../lib/setupReads";
import { clockTime } from "../lib/todayFeed";
import { refreshHiringSignalsStatus, useHiringSignalsStatus } from "../lib/useHiringSignalsStatus";
import { useBandFocus } from "../lib/useBandFocus";
import { useSetupReads } from "../lib/useSetupReads";

// Hiring signals (Hiring Signals P4) -- the standalone tab: recent public hiring
// posts for a role, with no company, for the hidden market (a founder or hiring
// manager posting "we're hiring" with no formal requisition anywhere).
//
// The page is the gate and the band. The server may have the feature switched off, and the shared
// status (GET /hiring-signals/status, read once for the whole app) says so before anything else is
// asked: enabled mounts the tab, disabled shows a calm "switched off" note, and a status request that
// itself failed is the page's own data failing -- the band says so and offers Try again. A person who
// reaches /hiring-signals directly on a server with the feature off gets an explanation, not a form
// that cannot work.
//
// THE MODEL LIVES HERE, not in the tab, because the next-step band reads it: whether a search is in
// flight, whether the results on the page are for what the boxes hold, whether that search is saved.
// Making it asks for nothing; the tab, mounted only once the status is "enabled", loads the two saved
// lists, so a server with the feature off never gets a hiring-signals request from this page.
//
// Requests besides the feature's own: GET /credentials, once, so the band knows what is missing before
// a search is made (the credentials say whether any of the four search keys is saved). No profile is
// read: nothing here needs one. The search itself is never gated on them: the server stays the authority, and a
// search that answers SETUP_REQUIRED is shown as it always was, with its link to the search keys.
//
// THE PAGE'S ONE CYAN BUTTON is the band's (lib/nextStep/pages/hiring-signals.ts): Search when ready,
// Save this search when results are on the page for a search that is not saved. The form's own Search
// and Save this search are outlined.

export default function HiringSignals() {
  const status = useHiringSignalsStatus();
  // This page needs no profile (see the header), so it never reads one and the band never sends a person to make one.
  const setup = useSetupReads({ profile: false });
  const [model] = useState(() => new HiringTabModel(apiFetch));
  const state = useSyncExternalStore(model.subscribe, model.getState, model.getState);

  const credentials = setup.reads.credentials;
  const searchKeys = credentials.kind === "ok" ? searchKeyCount(credentials.value) : null;

  // The client time a failure was seen, worked out once per failure: the status object, and the search
  // state, are replaced by a new one each time one happens.
  const statusFailedAt = useMemo(() => clockTime(new Date()), [status]);
  const searchFailedAt = useMemo(() => clockTime(new Date()), [state.search]);

  const facts = useMemo(() => hiringFactsOf({ status, state, searchKeys }), [status, state, searchKeys]);
  const outcome = useMemo(() => {
    const checks = setupChecks(setup.reads, setup.at);
    const input: NextStepInput = {
      page: "hiring-signals",
      signedIn: true,
      profileNeeded: false,
      credentials: checks.credentials,
      pageFailure: pageFailureOf({ status, state, statusFailedAt, searchFailedAt }),
      facts,
    };
    return nextStep(input);
  }, [setup.reads, setup.at, status, state, statusFailedAt, searchFailedAt, facts]);

  // Focus is held on the band until every request a band button started has answered, the setup reads
  // included (Try again on a failed setup check re-asks them).
  const checkingSetup = credentials.kind === "loading";
  const bandFocus = useBandFocus(state.search.kind !== "loading" && !state.savingSearch && status.kind !== "checking" && !checkingSetup);

  function onBandAction(intent: string) {
    // Every button of the band starts a request and is replaced while it runs: focus stays on the band.
    bandFocus.arm();
    switch (intent) {
      case "save-search":
        void model.saveCurrentSearch();
        return;
      case "retry-status":
        refreshHiringSignalsStatus();
        return;
      case "retry-search":
        if (retryableSearchFailure(state)) void model.retry();
        return;
      case "retry-setup":
        setup.retry();
        // The band holds one Try again at a time, and a failed setup check comes before a failed status
        // read: when both failed, this press asks for both, so neither is left without a way back.
        if (status.kind === "unavailable") refreshHiringSignalsStatus();
        return;
    }
  }

  return (
    <HiringSignalsPageView
      status={status}
      outcome={outcome}
      onBandAction={onBandAction}
      pending={state.savingSearch ? { intent: "save-search", label: "Saving..." } : null}
      saveSearchError={state.saveSearchError}
      onFormSubmit={bandFocus.armIfBandFocused}
    >
      <HiringSignalsTab model={model} keysMissing={searchKeys === 0} />
    </HiringSignalsPageView>
  );
}
