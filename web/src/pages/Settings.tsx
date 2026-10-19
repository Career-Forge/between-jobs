import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { ConnectBanner } from "../components/settings/ConnectBanner";
import { ConnectionsSection } from "../components/settings/ConnectionCards";
import { ModelProviderSection, type ProviderPanel } from "../components/settings/ModelProviderSection";
import { SearchKeysSection } from "../components/settings/SearchKeysSection";
import { NextStepBand } from "../components/paper/NextStepBand";
import { PageHeader } from "../components/paper/PageHeader";
import { FailureState, LoadingState } from "../components/paper/StateViews";
import { SavedSearchesCard } from "../components/SavedSearchesCard";
import { nextStep } from "../lib/nextStep";
import type { SettingsFacts, SettingsLlm } from "../lib/nextStep/pages/settings";
import { SEARCH_PROVIDER_CARDS } from "../lib/providerStatus";
import { knownDefaultProvider, llmRows, llmSetupState, providerName, searchRows } from "../lib/settings/llmProviders";
import { useOpenRouterConnect } from "../lib/settings/useOpenRouterConnect";
import { useProviderCatalog } from "../lib/settings/useProviderCatalog";
import { useSettingsData } from "../lib/settings/useSettingsData";
import { useCapabilities } from "../lib/useCapabilities";
import { useHiringSignalsEnabled } from "../lib/useHiringSignalsStatus";
import { useScrollToHash } from "../lib/useScrollToHash";
import "../styles/settings.css";

// Settings: the model provider, the search and data keys, and the accounts linked to this one. It is
// the page the API's setup errors send people to (it was the Integrations page at
// /profile/integrations, which redirects here), and the screen every new person uses to connect the
// model Between Jobs runs on. A person's own key is the only way anything here is paid for: nothing
// runs on a shared key.
//
// ONE PRIMARY ACTION. The next-step band holds the page's only cyan button. With no model key at
// all, that button is "Connect with OpenRouter"; every other button on the page, including the same
// one inside the AI-provider section and "Validate and save", is outlined. Which row applies is in
// lib/nextStep/pages/settings.ts, which also says why.
//
// SECTIONS (each a component under components/settings/):
//   AI provider           connected providers with their state, choosing a model, the default model,
//                         connecting another provider (OpenRouter in one click, or a pasted key)
//   Search and data keys  the optional keys, as rows with Connected / Not set
//   Connections           Telegram, Discord and Gmail
//   Saved searches        pause or delete a search that keeps running in the background
//
// Telegram and Discord show only on a server that has them (GET /capabilities), and Gmail's card
// follows what that says about this person: nothing here claims a connection that is not known.
//
// The tester-programme gate leaves this page open to someone who has not joined or has withdrawn:
// removing a key, disconnecting Gmail and pausing or deleting a saved search are how they switch off
// what keeps running in the background on their key (lib/enrollment.ts).

export default function Settings() {
  const capabilities = useCapabilities();
  const data = useSettingsData();
  const { catalog, retry: retryCatalog } = useProviderCatalog();
  // False while the answer is awaited, when it is "not enabled", and when asking failed.
  const hiringSignalsEnabled = useHiringSignalsEnabled();
  const connect = useOpenRouterConnect();
  const location = useLocation();
  const [now] = useState(() => new Date());
  const [panel, setPanel] = useState<ProviderPanel>(null);
  // What the polite live region says. `count` changes with every announcement, so the same words
  // said twice in a row ("x is now your default model." after choosing x again) still change what
  // the region holds and are read again: a live region only speaks when its content changes.
  const [announcement, setAnnouncement] = useState({ text: "", count: 0 });
  const announce = useCallback((text: string) => setAnnouncement((previous) => ({ text, count: previous.count + 1 })), []);
  // The provider whose model was chosen on this visit. The API has no route that says which model
  // is the default, so this is the one thing the page can know about it beyond a single key.
  const [chosenHere, setChosenHere] = useState<string | null>(null);
  // A key replaced on this visit came back with no model (the model it had belonged to its old
  // address), so the API may have cleared the default. Until a model is chosen the page claims none.
  const [droppedModelFor, setDroppedModelFor] = useState<string | null>(null);

  const rows = data.credentials.state === "ready" ? data.credentials.value : null;
  const providers = catalog.kind === "ready" ? catalog.providers : null;
  const llm = useMemo(() => (rows === null ? [] : llmRows(rows)), [rows]);
  const gmail = rows?.find((row) => row.service === "oauth" && row.provider === "gmail") ?? null;

  // A return from OpenRouter that worked: re-read the keys, then either choose a model (a first
  // connection) or say the model it had is unchanged (a reconnection). Once per result.
  const handled = useRef<unknown>(null);
  useEffect(() => {
    const result = connect.result;
    if (result.phase !== "connected" || handled.current === result) return;
    handled.current = result;
    const credential = result.credential;
    connect.dismiss();
    void (async () => {
      await data.reloadCredentials();
      if (credential !== null && llmSetupState(credential) === "ready") {
        announce("OpenRouter is connected again. Your model is unchanged.");
      } else {
        setPanel({ provider: "openrouter", panel: "model" });
        announce("OpenRouter is connected. Choose a model to finish.");
      }
    })();
  }, [connect, data, announce]);

  useScrollToHash(rows !== null, location.hash);

  const facts = useMemo<SettingsFacts | null>(() => {
    if (rows === null) return null;
    const states = llm.map((row) => ({ row, state: llmSetupState(row) }));
    const nameOf = (id: string) => providerName(id, providers);
    const ready = states.filter((entry) => entry.state === "ready");
    const dropped = droppedModelFor === null ? undefined : states.find((entry) => entry.row.provider === droppedModelFor && entry.state === "needs_model");
    const awaiting = dropped ?? states.find((entry) => entry.state === "needs_model") ?? states.find((entry) => entry.state === "needs_key");
    let llmFacts: SettingsLlm;
    if (ready.length > 0 && dropped === undefined) {
      // A default cleared on this visit is the `dropped` case above, so it is not passed here.
      llmFacts = { state: "ready", names: ready.map((entry) => nameOf(entry.row.provider)), defaultKnown: knownDefaultProvider(llm, chosenHere, false) !== null };
    } else if (awaiting !== undefined) llmFacts = { state: awaiting.state === "needs_model" ? "needs_model" : "needs_key", providerName: nameOf(awaiting.row.provider) };
    else llmFacts = { state: "none" };
    const connected = SEARCH_PROVIDER_CARDS.filter((card) => searchRows(rows).some((row) => row.provider === card.provider)).length;
    return {
      llm: llmFacts,
      search: { connected, total: SEARCH_PROVIDER_CARDS.length },
      // Only while that provider is still ready: a key removed since is not a model chosen.
      chosenHere: chosenHere !== null && ready.some((entry) => entry.row.provider === chosenHere) ? nameOf(chosenHere) : null,
    };
  }, [rows, llm, providers, chosenHere, droppedModelFor]);
  const defaultUnknown = droppedModelFor !== null && llm.some((row) => row.provider === droppedModelFor && llmSetupState(row) === "needs_model");

  const outcome = nextStep({
    page: "settings",
    signedIn: true,
    profile: data.profile,
    // Only whether the read worked. `modelKey` is left out on purpose: the global row that asks for a
    // key (G3) would link to this page from this page, and S1, S2 and S2k below say the same thing
    // with the buttons that act in place. Both read the keys with llmSetupState (lib/modelKey.ts).
    credentials: data.credentials.state === "ready" ? { state: "ready", value: {} } : data.credentials,
    facts: facts ?? undefined,
  });

  function onAction(intent: string) {
    switch (intent) {
      case "connect-openrouter":
        connect.start();
        return;
      case "choose-model": {
        const row = llm.find((candidate) => llmSetupState(candidate) === "needs_model");
        if (row !== undefined) setPanel({ provider: row.provider, panel: "model" });
        return;
      }
      case "replace-key": {
        const row = llm.find((candidate) => llmSetupState(candidate) === "needs_key");
        if (row !== undefined) setPanel({ provider: row.provider, panel: "replace" });
        return;
      }
      case "retry-setup":
      case "retry":
        void data.reloadAll();
        return;
    }
  }

  return (
    <div className="bj-settings">
      <PageHeader title="Settings" subtitle="The model Between Jobs runs on, the search keys you add, and the accounts you link. Everything here is read from what you've saved." />
      <NextStepBand outcome={outcome} onAction={onAction} pending={connect.busy ? { intent: "connect-openrouter", label: "Opening OpenRouter..." } : null} />
      <ConnectBanner result={connect.result} busy={connect.busy} onAgain={connect.start} onRetry={connect.retry} onDismiss={connect.dismiss} />
      <p className="bj-visually-hidden" role="status" aria-live="polite">
        {announcement.text}
        {announcement.count % 2 === 0 ? "" : "\u00a0"}
      </p>
      {announcement.text !== "" && (
        <p className="bj-set-success" aria-hidden="true">
          <span>{"✓"} </span>
          {announcement.text}
        </p>
      )}

      {data.credentials.state === "loading" && (
        <section className="bj-set-section" aria-label="Loading your keys">
          <LoadingState rows={3} rowHeight={72} label="Loading your saved keys..." />
        </section>
      )}
      {data.credentials.state === "failed" && (
        <section className="bj-set-section" aria-label="Your keys">
          <FailureState message="Couldn't read your saved keys, so none are shown. You didn't do anything wrong, and nothing was lost." />
        </section>
      )}
      {rows !== null && (
        <>
          {data.refreshFailed && (
            <FailureState
              message="That change went through, but your keys couldn't be read again, so what is shown below may be out of date."
              onRetry={() => void data.reloadCredentials()}
            />
          )}
          <ModelProviderSection
            credentials={rows}
            catalog={catalog}
            retryCatalog={retryCatalog}
            now={now}
            openRouter={{ start: connect.start, busy: connect.busy, error: connect.error, unsupported: connect.unsupported }}
            panel={panel}
            onPanel={setPanel}
            reload={data.reloadCredentials}
            onModelChosen={(providerId) => {
              setChosenHere(providerId);
              setDroppedModelFor(null);
            }}
            chosenHere={chosenHere}
            defaultUnknown={defaultUnknown}
            onModelDropped={(providerId) => {
              setDroppedModelFor(providerId);
              setChosenHere((current) => (current === providerId ? null : current));
            }}
            announce={announce}
          />
          <SearchKeysSection credentials={rows} hiringSignalsEnabled={hiringSignalsEnabled} now={now} onChanged={data.reloadCredentials} />
        </>
      )}
      <ConnectionsSection capabilities={capabilities} gmail={gmail} credentialsReady={rows !== null} onChanged={data.reloadCredentials} />
      <section id="saved-searches" className="bj-set-section" aria-label="Saved searches">
        <SavedSearchesCard />
      </section>
    </div>
  );
}
