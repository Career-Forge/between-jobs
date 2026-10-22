import { useState, type ReactNode } from "react";
import { apiFetch } from "../../lib/api";
import { formatTrailDate, mono } from "../../lib/trail";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { modelStatusWords } from "../../lib/modelKey";
import { llmSetupState, providerName, type LlmProvider } from "../../lib/settings/llmProviders";
import { settingsFailureMessage } from "../../lib/settings/settingsErrors";
import { StaticTrailLine } from "../paper/TrailLine";
import { ProviderFacts } from "./ProviderFacts";
import { ProviderNote } from "./ProviderNote";
import { RemoveKey } from "./RemoveKey";
import "../../styles/settings.css";

// One saved model key, as one quiet row: what is connected and what is missing, in words, then what
// can be done to it. The title IS the status (lib/modelKey.ts says it the same way Today does):
//
//   ready         "OpenRouter, model chosen"       with the model id under it, in mono
//   needs_model   "Choose a model for OpenRouter"  the key is checked and saved; nothing can run yet
//   needs_key     "Add a key for OpenRouter"       the key was not confirmed, so it is not usable
//
// The state is the server's `setup_state`, read with llmSetupState, and none of that wording is shown.
// A green check marks a model that is chosen and a gold diamond marks what is left to do; the words
// carry the state and the glyph is decoration. Where the page can know which key is the default
// (knownDefaultProvider), that row says "Default".
//
// The row's heading and its two panel buttons have ids (`provider-<id>-heading`, `-model-button`,
// `-replace-button`) so the section can put keyboard focus back on something after a panel closes.
//
// OpenRouter's row has the id `openrouter`, so /settings#openrouter scrolls to it (the connect form
// has it too while OpenRouter is not connected); the others are `provider-<id>`.
//
// The model id is the stored string, drawn as text. A trail line says who checked it and when; the
// "when" is the row's own `updated_at`, and a date that cannot be read is left out. A provider's
// data-location side-note (lib/providerNotes.ts) sits under its details, as plain text, except while
// its replace form is open: that form says it itself, for the region being chosen.

export interface ConnectedProviderRowProps {
  credential: CredentialSummary;
  /** The catalogue entry, when it has loaded; the row still works from the id alone without it. */
  provider: LlmProvider | undefined;
  /** An address the row shows when the provider has a saved host (the custom provider). */
  hostName: string | null;
  /** The region the row shows when the provider has a saved one (Qwen). */
  regionName?: string | null;
  /** This key is the default model, as far as the page can know (knownDefaultProvider). */
  isDefault?: boolean;
  /** The provider's data-location side-note for this key, or null (lib/providerNotes.ts). */
  note?: string | null;
  now: Date;
  /** Open ModelPicker / the replace form / neither, for this row. */
  panel: "model" | "replace" | null;
  onPanel: (panel: "model" | "replace" | null) => void;
  onRemoved: () => Promise<void>;
  /** The picker or the replace form, drawn under the row while one is open. */
  children?: ReactNode;
}

export function ConnectedProviderRow({ credential, provider, hostName, regionName = null, isDefault = false, note = null, now, panel, onPanel, onRemoved, children }: ConnectedProviderRowProps) {
  const name = provider?.name ?? providerName(credential.provider, null);
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

  const words = modelStatusWords(state, name);
  const nameAt = words.indexOf(name);
  return (
    <li className="bj-set-item" id={credential.provider === "openrouter" ? "openrouter" : `provider-${credential.provider}`} data-state={state}>
      <div className="bj-set-row">
        <div className="bj-set-row-main">
          <div className="bj-set-row-title">
            <span className={state === "ready" ? "bj-set-status-glyph bj-set-status-glyph--done" : "bj-set-status-glyph bj-set-status-glyph--todo"} aria-hidden="true">
              {state === "ready" ? "✓" : "◆"}
            </span>
            <h3 id={`provider-${credential.provider}-heading`} tabIndex={-1}>
              {words.slice(0, nameAt)}
              <span className="bj-set-status-name">{name}</span>
              {words.slice(nameAt + name.length)}
            </h3>
            {isDefault && <span className="bj-set-default-tag">Default</span>}
          </div>
          {state === "ready" && credential.model !== null && (
            <p className="bj-set-mono bj-set-model-line">{credential.model}</p>
          )}
          {state === "needs_model" && <p className="bj-set-hint">Your key is saved and checked. Choose a model to use it.</p>}
          {state === "needs_key" && <p className="bj-set-hint">This key isn't confirmed, so it can't be used. Replace it with a working one.</p>}
          {hostName !== null && <p className="bj-set-hint">Host: {hostName}</p>}
          {regionName !== null && <p className="bj-set-hint">Region: {regionName}</p>}
          <ProviderFacts providerId={credential.provider} providerName={name} />
          {note !== null && panel !== "replace" && <ProviderNote text={note} />}
          {credential.is_validated && (
            <StaticTrailLine trail={{ glyph: "code", actor: "Checked by code on save", when: when === null ? null : mono(when) }} />
          )}
        </div>
        <div className="bj-set-row-actions">
          {state !== "needs_key" && (
            <button type="button" id={`provider-${credential.provider}-model-button`} aria-expanded={panel === "model"} onClick={() => onPanel(panel === "model" ? null : "model")}>
              {state === "ready" ? "Change model" : "Choose a model"}
            </button>
          )}
          <button type="button" className="bj-quiet" id={`provider-${credential.provider}-replace-button`} aria-expanded={panel === "replace"} onClick={() => onPanel(panel === "replace" ? null : "replace")}>
            Replace key
          </button>
          <RemoveKey what={`${name} key`} holder={hostName ?? name} busy={removing} onRemove={() => void remove()} />
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
