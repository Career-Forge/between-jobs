import { useState, type ReactNode } from "react";
import { apiFetch } from "../../lib/api";
import { formatTrailDate, mono } from "../../lib/trail";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { llmSetupState, type LlmProvider } from "../../lib/settings/llmProviders";
import { settingsFailureMessage } from "../../lib/settings/settingsErrors";
import { StaticTrailLine } from "../paper/TrailLine";
import { ProviderFacts } from "./ProviderFacts";
import { RemoveKey } from "./RemoveKey";
import "../../styles/settings.css";

// One saved model key: which provider, where it stands, the model it uses, and what can be done to
// it. The state is the server's `setup_state`:
//
//   ready         "✓ Validated": checked with the provider, and a model is chosen
//   needs_model   "needs a model" (gold): checked and saved, unusable until a model is chosen
//   needs_key     "needs a key" (gold): not checked, so not usable
//
// OpenRouter's row has the id `openrouter`, the anchor a link to /settings#openrouter lands on (the
// connect form has it too while OpenRouter is not connected); the others are `provider-<id>`.
//
// The model id is the stored string, drawn as text. A trail line says who checked it and when; the
// "when" is the row's own `updated_at`, and a date that cannot be read is left out.

export interface ConnectedProviderRowProps {
  credential: CredentialSummary;
  /** The catalogue entry, when it has loaded; the row still works from the id alone without it. */
  provider: LlmProvider | undefined;
  /** An address the row shows when the provider has a saved host (the custom provider). */
  hostName: string | null;
  now: Date;
  /** Open ModelPicker / the replace form / neither, for this row. */
  panel: "model" | "replace" | null;
  onPanel: (panel: "model" | "replace" | null) => void;
  onRemoved: () => Promise<void>;
  /** The picker or the replace form, drawn under the row while one is open. */
  children?: ReactNode;
}

export function ConnectedProviderRow({ credential, provider, hostName, now, panel, onPanel, onRemoved, children }: ConnectedProviderRowProps) {
  const name = provider?.name ?? credential.provider;
  const state = llmSetupState(credential);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const when = formatTrailDate(credential.updated_at, { now });

  async function remove() {
    setRemoving(true);
    setError(null);
    try {
      await apiFetch(`/credentials/llm/${encodeURIComponent(credential.provider)}`, { method: "DELETE" });
      await onRemoved();
    } catch (e) {
      setError(settingsFailureMessage(e, { action: "remove_key", provider: name }, "Couldn't remove that key."));
      setRemoving(false);
    }
  }

  return (
    <li className="bj-set-item" id={credential.provider === "openrouter" ? "openrouter" : `provider-${credential.provider}`} data-state={state}>
      <div className="bj-set-row">
        <div className="bj-set-row-main">
          <div className="bj-set-row-title">
            <h3>{name}</h3>
            {state === "ready" && <span className="bj-badge-emerald">{"✓"} Validated</span>}
            {state === "needs_model" && <span className="bj-badge-gold">Needs a model</span>}
            {state === "needs_key" && <span className="bj-badge-gold">Needs a key</span>}
          </div>
          {state === "ready" && credential.model !== null && (
            <p className="bj-set-mono bj-set-model-line">{credential.model}</p>
          )}
          {state === "needs_model" && <p className="bj-set-hint">Your key is saved and checked. Choose a model to use it.</p>}
          {state === "needs_key" && <p className="bj-set-hint">This key isn't confirmed, so it can't be used. Replace it with a working one.</p>}
          {hostName !== null && <p className="bj-set-hint">Host: {hostName}</p>}
          <ProviderFacts providerId={credential.provider} providerName={name} />
          {credential.is_validated && (
            <StaticTrailLine trail={{ glyph: "code", actor: "Checked by code on save", when: when === null ? null : mono(when) }} />
          )}
        </div>
        <div className="bj-set-row-actions">
          {state !== "needs_key" && (
            <button type="button" aria-expanded={panel === "model"} onClick={() => onPanel(panel === "model" ? null : "model")}>
              {state === "ready" ? "Change model" : "Choose a model"}
            </button>
          )}
          <button type="button" aria-expanded={panel === "replace"} onClick={() => onPanel(panel === "replace" ? null : "replace")}>
            Replace key
          </button>
          <RemoveKey what={`${name} key`} holder={name} busy={removing} onRemove={() => void remove()} />
          {provider?.manageUrl != null && (
            <a href={provider.manageUrl} target="_blank" rel="noopener noreferrer">
              Manage on {name}
              <span className="bj-visually-hidden"> (opens in a new tab)</span>
            </a>
          )}
        </div>
      </div>
      {error !== null && (
        <p className="bj-set-error" role="alert">
          {error}
        </p>
      )}
      {children}
    </li>
  );
}
