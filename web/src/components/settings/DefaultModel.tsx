import { useId, useState } from "react";
import { apiFetch } from "../../lib/api";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { knownDefaultProvider, llmSetupState, providerName, type LlmProvider } from "../../lib/settings/llmProviders";
import { settingsFailureMessage } from "../../lib/settings/settingsErrors";
import "../../styles/settings.css";

// The default model: the one Between Jobs calls on the person's key. Choosing a model for a key
// makes it the default (PUT /credentials/llm/{provider}/model), so changing the default means
// choosing among the keys that are ready.
//
// WHAT THE PAGE CAN KNOW. The API stores the default but has no route that reads it back, and a key's
// row does not say whether it is the default. So:
//   - with one ready key, that key and its model are the default (the API repoints the default to
//     the only other ready key when one is removed, and clears it when there is none) -- unless
//     another key is waiting for a model, which the API may have cleared the default for;
//   - with several, which one is the default is not known here until it is chosen on this visit;
//   - with none, there is nothing to show;
//   - after a key was replaced at another address and lost its model, the API may have cleared the
//     default, so the page claims none until one is chosen (`defaultUnknown`).
// The page says which of these it is rather than guessing (`knownDefaultProvider` in
// lib/settings/llmProviders.ts decides, and the next-step band reads the same answer).
//
// Making a key the default again sends the same one tiny real request choosing a model does, so a
// model the provider has since retired is found out here and not on the next draft.

export interface DefaultModelProps {
  credentials: readonly CredentialSummary[];
  catalog: readonly LlmProvider[] | null;
  /** The provider made the default on this visit, if any. */
  chosenHere: string | null;
  /**
   * The page has reason to think the stored default is gone even though a key is ready (a key
   * was replaced at another address and lost its model): it claims no default until one is chosen.
   */
  defaultUnknown?: boolean;
  onChanged: (row: CredentialSummary | null, providerId: string) => void;
}

export function DefaultModel({ credentials, catalog, chosenHere, defaultUnknown = false, onChanged }: DefaultModelProps) {
  const ready = credentials.filter((row) => llmSetupState(row) === "ready" && row.model !== null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectId = useId();
  const errorId = useId();

  if (ready.length === 0) return null;

  const knownProvider = knownDefaultProvider(credentials, chosenHere, defaultUnknown);
  const knownDefault = ready.find((row) => row.provider === knownProvider) ?? null;
  const canChoose = knownDefault === null || ready.length > 1;

  async function setDefault() {
    const row = ready.find((candidate) => candidate.provider === pick);
    if (row === undefined || row.model === null || busy) return;
    const name = providerName(row.provider, catalog);
    setBusy(true);
    setError(null);
    try {
      const saved = await apiFetch<unknown>(`/credentials/llm/${encodeURIComponent(row.provider)}/model`, {
        method: "PUT",
        body: JSON.stringify({ model: row.model }),
      });
      onChanged((saved as CredentialSummary | null) ?? null, row.provider);
      setPick("");
    } catch (e) {
      setError(settingsFailureMessage(e, { action: "choose_model", provider: name }, "Couldn't change the default model."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bj-set-card bj-set-default" id="default-model">
      <h3>Default model</h3>
      {knownDefault !== null && knownDefault.model !== null ? (
        <p>
          <span className="bj-set-mono">{knownDefault.model}</span> on {providerName(knownDefault.provider, catalog)}. This is the model Between Jobs calls
          on your key.
        </p>
      ) : (
        <p>
          {ready.length === 1
            ? "You have 1 connected model, and this page can't tell whether it is your default. Choose it to make it the default."
            : `You have ${ready.length} connected models, and this page can't tell which one is your default. Choose one to make it the default.`}
        </p>
      )}
      {canChoose && (
        <>
          <div className="bj-set-field">
            <label htmlFor={selectId}>Use as the default</label>
            <select id={selectId} value={pick} aria-describedby={error === null ? undefined : errorId} onChange={(event) => setPick(event.target.value)}>
              <option value="">Choose a connected model</option>
              {ready.map((row) => (
                <option key={row.provider} value={row.provider}>
                  {providerName(row.provider, catalog)} -- {row.model}
                </option>
              ))}
            </select>
          </div>
          {error !== null && (
            <p id={errorId} className="bj-set-error" role="alert">
              {error}
            </p>
          )}
          {busy && (
            <p className="bj-set-status" role="status">
              Checking the model: one tiny real request...
            </p>
          )}
          <div className="bj-set-actions">
            <button type="button" className="bj-set-action" disabled={pick === "" && !busy} aria-disabled={busy} onClick={() => void setDefault()}>
              {busy ? "Checking..." : "Make default"}
            </button>
          </div>
          <p className="bj-set-hint">Making a model the default sends it one tiny real request first, the same as choosing it did.</p>
        </>
      )}
    </div>
  );
}
