import { useEffect, useState } from "react";
import type { CatalogState } from "../../lib/settings/useProviderCatalog";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { OPENROUTER_ID, endpointForAddress, llmRows, providerName, regionForAddress, type LlmProvider } from "../../lib/settings/llmProviders";
import { FailureState, LoadingState } from "../paper/StateViews";
import { ConnectedProviderRow } from "./ConnectedProviderRow";
import { DefaultModel } from "./DefaultModel";
import { ModelPicker } from "./ModelPicker";
import { ProviderConnectCard } from "./ProviderConnectCard";
import "../../styles/settings.css";

// "AI provider": which model providers the person has connected and where each stands, the default
// model, and a way to add another. Everything the person does here is one of three steps, each its
// own control: save a key (ProviderConnectCard), choose a model for it (ModelPicker), and, if more
// than one is ready, say which is the default (DefaultModel).
//
// The row of a key that was just saved opens its model picker by itself: a key with no model is
// "needs a model", never a silent default, and the next thing to do is obvious.
//
// KEYBOARD FOCUS. A panel that closes, a key that is replaced or removed, each takes away the element
// that had focus. Focus is put back on purpose: on the row's heading after a change, on the button
// that opened a panel when it is closed without a change, and on the section heading when the row
// is gone.

export type ProviderPanel = { provider: string; panel: "model" | "replace" } | null;

export interface OpenRouterConnect {
  start: () => void;
  busy: boolean;
  error: string | null;
  unsupported: boolean;
}

export interface ModelProviderSectionProps {
  credentials: readonly CredentialSummary[];
  catalog: CatalogState;
  retryCatalog: () => void;
  now: Date;
  openRouter: OpenRouterConnect;
  /** Which row has its model picker or replace form open (lifted so the next-step band can open one). */
  panel: ProviderPanel;
  onPanel: (panel: ProviderPanel) => void;
  /** The key list changed; resolves once it has been read again. */
  reload: () => Promise<void>;
  /** A model was proved and stored for this provider. */
  onModelChosen: (providerId: string, model: string) => void;
  /** The provider made the default on this visit. */
  chosenHere: string | null;
  /** The stored default may be gone (a key was replaced at another address and lost its model). */
  defaultUnknown?: boolean;
  /** A replaced key came back with no model: the model it had belonged to its old address. */
  onModelDropped?: (providerId: string) => void;
  /** Words for a polite announcement ("Connected OpenAI."). */
  announce: (message: string) => void;
}

function hostNameOf(credential: CredentialSummary, provider: LlmProvider | undefined): string | null {
  return endpointForAddress(provider, credential.base_url)?.name ?? null;
}

function regionNameOf(credential: CredentialSummary, provider: LlmProvider | undefined): string | null {
  return regionForAddress(provider, credential.base_url)?.name ?? null;
}

export function ModelProviderSection({
  credentials,
  catalog,
  retryCatalog,
  now,
  openRouter,
  panel,
  onPanel,
  reload,
  onModelChosen,
  chosenHere,
  defaultUnknown = false,
  onModelDropped,
  announce,
}: ModelProviderSectionProps) {
  const providers = catalog.kind === "ready" ? catalog.providers : null;
  const connected = llmRows(credentials);
  const connectedIds = new Set(connected.map((row) => row.provider));
  const available = providers === null ? [] : providers.filter((provider) => !connectedIds.has(provider.id));
  const [chip, setChip] = useState<string | null>(null);
  const selected = available.find((provider) => provider.id === chip) ?? available.find((provider) => provider.id === OPENROUTER_ID) ?? available[0];
  // Where keyboard focus goes once the change just made has been drawn (an element id).
  const [focusRequest, setFocusRequest] = useState<{ id: string } | null>(null);
  useEffect(() => {
    if (focusRequest !== null) document.getElementById(focusRequest.id)?.focus();
  }, [focusRequest]);
  const focusOn = (id: string) => setFocusRequest({ id });

  async function afterSave(provider: LlmProvider, row: CredentialSummary | null) {
    await reload();
    const hasModel = row !== null && typeof row.model === "string" && row.model !== "";
    if (hasModel) {
      onPanel(null);
      onModelChosen(provider.id, row.model as string);
      announce(`${provider.name} is connected and ready.`);
      focusOn(`provider-${provider.id}-heading`);
    } else {
      onPanel({ provider: provider.id, panel: "model" });
      announce(`${provider.name} is connected. Choose a model to finish.`);
    }
  }

  async function afterReplace(provider: LlmProvider, row: CredentialSummary | null) {
    await reload();
    if (row !== null && (typeof row.model !== "string" || row.model === "")) {
      // The key now goes to another address (or never had a model): the API kept no model for it
      // and may have cleared the default. Say so, and go straight to choosing one.
      onModelDropped?.(provider.id);
      onPanel({ provider: provider.id, panel: "model" });
      announce(`Your ${provider.name} key was replaced. Choose a model to finish.`);
      return;
    }
    onPanel(null);
    announce(`Your ${provider.name} key was replaced.`);
    focusOn(`provider-${provider.id}-heading`);
  }

  async function afterModel(providerId: string, model: string) {
    await reload();
    onPanel(null);
    onModelChosen(providerId, model);
    announce(`${model} is now your default model.`);
    focusOn(`provider-${providerId}-heading`);
  }

  return (
    <section id="ai-provider" className="bj-set-section" aria-labelledby="ai-provider-h">
      <h2 id="ai-provider-h" tabIndex={-1}>
        AI provider
      </h2>
      <p className="bj-set-lede">
        Connect a provider once. Saving always sends one real request to it, so a key it refuses is never stored. Between Jobs has no model of its
        own and never runs on a shared key.
      </p>

      {connected.length > 0 && (
        <ul className="bj-set-list" aria-label="Connected model providers">
          {connected.map((credential) => {
            const provider = providers?.find((candidate) => candidate.id === credential.provider);
            const name = providerName(credential.provider, providers);
            const open = panel?.provider === credential.provider ? panel.panel : null;
            return (
              <ConnectedProviderRow
                key={credential.provider}
                credential={credential}
                provider={provider}
                hostName={hostNameOf(credential, provider)}
                regionName={regionNameOf(credential, provider)}
                now={now}
                panel={open}
                onPanel={(next) => onPanel(next === null ? null : { provider: credential.provider, panel: next })}
                onRemoved={async () => {
                  onPanel(null);
                  await reload();
                  announce(`${name} was removed.`);
                  focusOn("ai-provider-h");
                }}
              >
                {open === "model" && (
                  <ModelPicker
                    providerId={credential.provider}
                    providerName={name}
                    currentModel={credential.model}
                    onChosen={(_row, model) => void afterModel(credential.provider, model)}
                    onClose={() => {
                      onPanel(null);
                      focusOn(`provider-${credential.provider}-model-button`);
                    }}
                  />
                )}
                {open === "replace" && provider !== undefined && (
                  <ProviderConnectCard
                    provider={provider}
                    mode="replace"
                    autoFocus
                    savedBaseUrl={credential.base_url}
                    openRouter={provider.id === OPENROUTER_ID ? openRouter : undefined}
                    onSaved={(saved) => void afterReplace(provider, saved)}
                    onCancel={() => {
                      onPanel(null);
                      focusOn(`provider-${credential.provider}-replace-button`);
                    }}
                  />
                )}
                {open === "replace" && provider === undefined && (
                  <p className="bj-set-notice">The list of providers isn't loaded, so the key form can't be shown yet. Reload the list below, then try again.</p>
                )}
              </ConnectedProviderRow>
            );
          })}
        </ul>
      )}

      <DefaultModel
        credentials={connected}
        catalog={providers}
        chosenHere={chosenHere}
        defaultUnknown={defaultUnknown}
        onChanged={(_row, providerId) => {
          const model = connected.find((row) => row.provider === providerId)?.model ?? "";
          onModelChosen(providerId, model);
          announce(`${providerName(providerId, providers)} is now your default.`);
        }}
      />

      <div className="bj-set-add">
        <h3>{connected.length === 0 ? "Connect a provider" : "Add another provider"}</h3>
        {catalog.kind === "loading" && <LoadingState rows={2} rowHeight={44} label="Loading the list of providers..." />}
        {catalog.kind === "failed" && <FailureState message="Couldn't load the list of providers, so none can be added yet." onRetry={retryCatalog} retryLabel="Try again" />}
        {catalog.kind === "ready" && available.length === 0 && <p className="bj-set-hint">Every provider Between Jobs supports is connected.</p>}
        {selected !== undefined && (
          <>
            <div className="bj-segmented bj-set-chips" role="group" aria-label="Choose a provider to connect">
              {available.map((provider) => (
                <button
                  key={provider.id}
                  type="button"
                  className="bj-selectable"
                  aria-pressed={provider.id === selected.id}
                  onClick={() => setChip(provider.id)}
                >
                  {provider.name}
                </button>
              ))}
            </div>
            <ProviderConnectCard
              key={selected.id}
              provider={selected}
              mode="add"
              openRouter={selected.id === OPENROUTER_ID ? openRouter : undefined}
              onSaved={(row) => void afterSave(selected, row)}
            />
          </>
        )}
      </div>
    </section>
  );
}
