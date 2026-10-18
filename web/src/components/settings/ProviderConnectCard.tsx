import { useEffect, useId, useRef, useState } from "react";
import { apiFetch } from "../../lib/api";
import { parseCredentialRows, type CredentialSummary } from "../../lib/settings/credentials";
import { CUSTOM_ID, OPENROUTER_ID, QWEN_ID, isModelId, type LlmProvider } from "../../lib/settings/llmProviders";
import { settingsFailureMessage } from "../../lib/settings/settingsErrors";
import { KeyField } from "./KeyField";
import { ProviderFacts } from "./ProviderFacts";
import "../../styles/settings.css";

// Connecting one model provider, or replacing its key. Two ways in:
//
//   OpenRouter only   "Connect with OpenRouter": sign in there, press Allow, come back. No key to
//                     copy. (The browser half of the OAuth flow is lib/settings/openrouterOAuth.ts.)
//   every provider    paste a key: a masked field, a link to where that provider issues one, and
//                     "Validate and save", which has the API check the key with the provider
//                     before anything is stored.
//
// Qwen also takes a region and, for a Model Studio region, a workspace id (the API builds the
// address from them). The custom provider takes its address from the short list the API allows and
// never from free text. A provider with no free way to check a key (custom and Qwen may have none)
// asks for a model id too, and only when the API says so.
//
// SAVING A KEY IS NOT CHOOSING A MODEL. The key is stored with no model unless one was needed to
// check it; the model is the next step (ModelPicker), because the list of models comes from the key.
//
// THE KEY LEAVES THE PAGE WHEN IT IS SAVED: the field is emptied (and hidden again) the moment the
// API accepts it, and nothing on the page ever shows a saved key back.

export interface ProviderConnectCardProps {
  provider: LlmProvider;
  mode: "add" | "replace";
  /** Move focus into the form when it opens (it was opened by a button, not by choosing a chip). */
  autoFocus?: boolean;
  /** Present for OpenRouter: the one-click sign-in. */
  openRouter?: { start: () => void; busy: boolean; error: string | null; unsupported: boolean };
  /** The row the API returned, once the key is stored (null if the reply could not be read). */
  onSaved: (row: CredentialSummary | null) => void;
  onCancel?: () => void;
}

export function ProviderConnectCard({ provider, mode, autoFocus, openRouter, onSaved, onCancel }: ProviderConnectCardProps) {
  const [secret, setSecret] = useState("");
  const [regionId, setRegionId] = useState(provider.regions[0]?.id ?? "");
  const [workspaceId, setWorkspaceId] = useState("");
  const [endpointId, setEndpointId] = useState(provider.endpoints[0]?.id ?? "");
  const [askModel, setAskModel] = useState(false);
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const modelInput = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLFormElement>(null);
  const errorId = useId();
  const headingId = useId();
  const regionSelectId = useId();
  const workspaceInputId = useId();
  const endpointSelectId = useId();
  const modelInputId = useId();

  const region = provider.regions.find((candidate) => candidate.id === regionId) ?? provider.regions[0];
  const endpoint = provider.endpoints.find((candidate) => candidate.id === endpointId) ?? provider.endpoints[0];
  const needsWorkspace = provider.id === QWEN_ID && region?.needsWorkspace === true;
  const getKeyUrl = region?.getKeyUrl ?? endpoint?.getKeyUrl ?? provider.getKeyUrl;

  useEffect(() => {
    if (autoFocus === true) root.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    if (askModel) modelInput.current?.focus();
  }, [askModel]);

  async function save() {
    if (saving) return;
    const key = secret.trim();
    if (key === "") return setError(`Paste your ${provider.name} key first.`);
    if (needsWorkspace && workspaceId.trim() === "") return setError("This region needs your Model Studio workspace id.");
    if (provider.id === CUSTOM_ID && endpoint === undefined) return setError("Choose which provider's address to use.");
    if (askModel && model.trim() !== "" && !isModelId(model.trim())) return setError("That doesn't look like a model id. Copy it exactly as the provider shows it.");
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, string> = { service: "llm", provider: provider.id, secret: key };
      if (provider.id === QWEN_ID && region !== undefined) {
        body.region = region.id;
        if (needsWorkspace) body.workspace_id = workspaceId.trim();
      }
      if (provider.id === CUSTOM_ID && endpoint !== undefined) body.base_url = endpoint.baseUrl;
      if (askModel && model.trim() !== "") body.model = model.trim();
      const saved = await apiFetch<unknown>("/credentials", { method: "POST", body: JSON.stringify(body) });
      // The key is stored: it leaves the page now, before anything else happens.
      setSecret("");
      setWorkspaceId("");
      onSaved(parseCredentialRows([saved])?.[0] ?? null);
    } catch (e) {
      setError(settingsFailureMessage(e, { action: "save_key", provider: provider.name }, "Couldn't save that key."));
      if ((e as { reason?: unknown }).reason === "model_required_to_check_key") setAskModel(true);
    } finally {
      setSaving(false);
    }
  }

  const title = mode === "replace" ? `Replace your ${provider.name} key` : `Connect ${provider.name}`;
  const oneClick = provider.id === OPENROUTER_ID && openRouter !== undefined && !openRouter.unsupported;

  return (
    <form
      ref={root}
      tabIndex={-1}
      className="bj-set-card bj-set-connect"
      method="post"
      noValidate
      aria-labelledby={headingId}
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="bj-set-card-head">
        <h3 id={headingId}>{title}</h3>
        {getKeyUrl !== null && (
          <a href={getKeyUrl} target="_blank" rel="noopener noreferrer">
            Get your key at {provider.name}
            <span className="bj-visually-hidden"> (opens in a new tab)</span>
          </a>
        )}
      </div>
      <ProviderFacts providerId={provider.id} providerName={provider.name} />

      {oneClick && (
        <>
          <div className="bj-set-oneclick">
            <button type="button" className="bj-set-action" aria-disabled={openRouter.busy} onClick={() => !openRouter.busy && openRouter.start()}>
              {openRouter.busy ? "Opening OpenRouter..." : "Connect with OpenRouter"}
            </button>
            <p className="bj-set-hint">You sign in at OpenRouter, press Allow, and come straight back. No key to copy.</p>
          </div>
          {openRouter.error !== null && (
            <p className="bj-set-error" role="alert">
              {openRouter.error}
            </p>
          )}
          <p className="bj-set-or" aria-hidden="true">
            or paste a key
          </p>
        </>
      )}

      {provider.id === QWEN_ID && provider.regions.length > 0 && (
        <div className="bj-set-field">
          <label htmlFor={regionSelectId}>Region</label>
          <select id={regionSelectId} value={region?.id ?? ""} onChange={(event) => setRegionId(event.target.value)}>
            {provider.regions.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
          <p className="bj-set-hint">A key only works in the region it was created in. Pick the one your key came from.</p>
        </div>
      )}
      {needsWorkspace && (
        <div className="bj-set-field">
          <label htmlFor={workspaceInputId}>Workspace ID</label>
          <input
            id={workspaceInputId}
            type="text"
            className="bj-set-mono-input"
            value={workspaceId}
            placeholder="From your Model Studio console"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setWorkspaceId(event.target.value)}
          />
        </div>
      )}

      {provider.id === CUSTOM_ID && provider.endpoints.length > 0 && (
        <div className="bj-set-field">
          <label htmlFor={endpointSelectId}>Base URL</label>
          <select id={endpointSelectId} value={endpoint?.id ?? ""} onChange={(event) => setEndpointId(event.target.value)}>
            {provider.endpoints.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name} -- {candidate.baseUrl}
              </option>
            ))}
          </select>
          <p className="bj-set-hint">
            Only these OpenAI-compatible hosts are accepted ({provider.endpoints.map((candidate) => candidate.name).join(", ")}). Your key is
            sent to the one you pick and nowhere else. A host that isn't on the list can't be added here.
          </p>
        </div>
      )}

      <KeyField
        label="API key"
        value={secret}
        onChange={setSecret}
        placeholder="Paste your key"
        hint={provider.keyHint === "" ? undefined : provider.keyHint}
        disabled={saving}
        describedBy={error === null ? undefined : errorId}
      />

      {askModel && (
        <div className="bj-set-field">
          <label htmlFor={modelInputId}>Model id</label>
          <input
            id={modelInputId}
            ref={modelInput}
            type="text"
            className="bj-set-mono-input"
            value={model}
            placeholder="provider/model-name"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setModel(event.target.value)}
          />
          <p className="bj-set-hint">{provider.name} has no free way to check a key, so it is checked with one tiny request to this model.</p>
        </div>
      )}

      {error !== null && (
        <p id={errorId} className="bj-set-error" role="alert">
          {error}
        </p>
      )}
      {saving && (
        <p className="bj-set-status" role="status">
          Checking your key with {provider.name}...
        </p>
      )}

      <div className="bj-set-actions">
        <button type="submit" className="bj-set-action" aria-disabled={saving}>
          {saving ? "Validating..." : "Validate and save"}
        </button>
        {onCancel !== undefined && (
          <button type="button" className="bj-quiet" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      <p className="bj-set-hint">
        Saving checks the key with {provider.name} first, so a key it refuses is never stored. It is kept encrypted and used to make calls to{" "}
        {provider.name} for you.
      </p>
    </form>
  );
}
