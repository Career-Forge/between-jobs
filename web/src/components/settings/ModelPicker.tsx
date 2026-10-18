import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { apiFetch } from "../../lib/api";
import { LoadingState } from "../paper/StateViews";
import { parseCredentialRows, type CredentialSummary } from "../../lib/settings/credentials";
import {
  MODEL_ROWS_SHOWN,
  filterModels,
  formatContext,
  isModelId,
  parseModelList,
  type ModelList,
} from "../../lib/settings/llmProviders";
import { settingsFailureMessage } from "../../lib/settings/settingsErrors";
import "../../styles/settings.css";

// Choosing the model for a key that is already saved. A separate step on purpose: the models a key
// can use come from the key, and a key with no model is "needs a model", never a silent default.
//
//   load     POST /credentials/llm/models with the saved key (the key never leaves the server)
//   choose   the person picks a row or types an id, then "Use this model"
//   prove    PUT /credentials/llm/{provider}/model sends one tiny real request to the provider; only
//            when it accepts the model is it stored and made the default. A refusal changes nothing.
//
// THE LIST IS A STRANGER'S TEXT. Ids and names come from the provider's own answer, relayed. They are
// drawn as text and nowhere else (React escapes them; they are never markup, a link or a handler),
// the ids the server would refuse are not offered, and the long list is cut to its first hundred
// matches with a filter to reach the rest.
//
// The list can be missing in three honest ways, each said in words: the provider has no list route
// (Qwen and the custom hosts may not), the list was cut at the server's 500, or the request failed.
// In every case a model id can be typed: the escape hatch is always there.

export interface ModelPickerProps {
  providerId: string;
  providerName: string;
  /** The model the key has now, if any. */
  currentModel: string | null;
  /** The row the API returned once the model was proved and stored. */
  onChosen: (row: CredentialSummary | null, model: string) => void;
  onClose?: () => void;
}

type ListState = { kind: "loading" } | { kind: "failed"; message: string } | { kind: "ready"; list: ModelList };

export function ModelPicker({ providerId, providerName, currentModel, onChosen, onClose }: ModelPickerProps) {
  const [load, setLoad] = useState<ListState>({ kind: "loading" });
  const [query, setQuery] = useState("");
  const [chosenId, setChosenId] = useState(currentModel ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const requestNumber = useRef(0);
  const root = useRef<HTMLDivElement>(null);
  // The name can change while the catalogue loads (the id is shown until it has); that must not
  // send the list request again.
  const nameRef = useRef(providerName);
  nameRef.current = providerName;
  const filterId = useId();
  const typedId = useId();
  const errorId = useId();
  const groupName = useId();

  const fetchModels = useCallback(async () => {
    const mine = ++requestNumber.current;
    setLoad({ kind: "loading" });
    try {
      const body = await apiFetch<unknown>("/credentials/llm/models", { method: "POST", body: JSON.stringify({ provider: providerId }) });
      const list = parseModelList(body);
      if (mine !== requestNumber.current) return;
      setLoad(
        list === null
          ? { kind: "failed", message: `${nameRef.current} answered, but in a form we couldn't read. You can still type a model id below.` }
          : { kind: "ready", list },
      );
    } catch (e) {
      if (mine !== requestNumber.current) return;
      setLoad({ kind: "failed", message: settingsFailureMessage(e, { action: "list_models", provider: nameRef.current }, "Couldn't load the model list.") });
    }
  }, [providerId]);

  // It opens because the person asked for it, or because the key they just saved needs a model:
  // either way that is where they are going next.
  useEffect(() => {
    root.current?.focus();
  }, []);

  useEffect(() => {
    void fetchModels();
    return () => {
      requestNumber.current += 1;
    };
  }, [fetchModels]);

  const list = load.kind === "ready" ? load.list : null;
  const matches = useMemo(() => (list === null ? [] : filterModels(list.models, query)), [list, query]);
  const shown = matches.slice(0, MODEL_ROWS_SHOWN);
  const candidate = chosenId.trim();
  const valid = candidate !== "" && isModelId(candidate);

  async function choose() {
    if (busy || !valid) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const row = await apiFetch<unknown>(`/credentials/llm/${encodeURIComponent(providerId)}/model`, {
        method: "PUT",
        body: JSON.stringify({ model: candidate }),
      });
      setDone(candidate);
      onChosen(parseCredentialRows([row])?.[0] ?? null, candidate);
    } catch (e) {
      setError(settingsFailureMessage(e, { action: "choose_model", provider: providerName }, "Couldn't use that model."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={root} tabIndex={-1} className="bj-set-card bj-set-picker" aria-busy={load.kind === "loading"}>
      <div className="bj-set-card-head">
        <h3>{currentModel === null ? `Choose a ${providerName} model` : `Change the ${providerName} model`}</h3>
        {onClose !== undefined && (
          <button type="button" className="bj-quiet" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {currentModel === null && <p className="bj-set-hint">Your key is saved and checked. It can't be used until you choose a model.</p>}

      {load.kind === "loading" && <LoadingState rows={4} rowHeight={48} label={`Loading ${providerName}'s models...`} />}

      {load.kind === "failed" && (
        <div className="bj-set-notice bj-set-notice--failure" role="alert">
          <p>
            <span className="bj-state-glyph" aria-hidden="true">
              {"✕"}
            </span>
            {load.message}
          </p>
          <button type="button" onClick={() => void fetchModels()}>
            Try loading the list again
          </button>
        </div>
      )}

      {list !== null && list.listing === "unavailable" && (
        <p className="bj-set-notice">
          {providerName} doesn't give out a list of its models for this key, so none can be shown. Type the model id exactly as {providerName} shows it.
        </p>
      )}

      {list !== null && list.listing === "live" && list.models.length === 0 && (
        <p className="bj-set-notice">{providerName} returned no models for this key. Type a model id below if you know one.</p>
      )}

      {list !== null && list.models.length > 0 && (
        <>
          <div className="bj-set-field">
            <label htmlFor={filterId}>Filter models</label>
            <input
              id={filterId}
              type="search"
              value={query}
              placeholder="Type part of a name or id"
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <p className="bj-set-hint" role="status">
            {matches.length === 0
              ? "No model matches that."
              : matches.length > MODEL_ROWS_SHOWN
                ? `Showing the first ${MODEL_ROWS_SHOWN} of ${matches.length} matches, newest first. Filter to narrow it.`
                : `${matches.length} ${matches.length === 1 ? "model" : "models"}, newest first.`}
          </p>
          {list.truncated && (
            <p className="bj-set-notice">
              {providerName} has more models than this list holds: it is cut to the newest {list.models.length}. If the one you want isn't here, type its
              id below.
            </p>
          )}
          {shown.length > 0 && (
            <fieldset className="bj-set-models-group">
              <legend className="bj-visually-hidden">{providerName} models</legend>
              <ul className="bj-set-models">
                {shown.map((model) => {
                  const context = formatContext(model.contextLength);
                  return (
                    <li key={model.id}>
                      <label className="bj-set-model">
                        <input
                          type="radio"
                          name={groupName}
                          value={model.id}
                          checked={candidate === model.id}
                          onChange={() => {
                            setChosenId(model.id);
                            setError(null);
                          }}
                        />
                        <span className="bj-set-model-text">
                          {model.name !== "" && <span className="bj-set-model-name">{model.name}</span>}
                          <span className="bj-set-mono">{model.id}</span>
                        </span>
                        {context !== null && <span className="bj-set-mono bj-set-model-context">{context}</span>}
                      </label>
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          )}
        </>
      )}

      <div className="bj-set-field">
        <label htmlFor={typedId}>{list !== null && list.models.length > 0 ? "Or type a model id" : "Model id"}</label>
        <input
          id={typedId}
          type="text"
          className="bj-set-mono-input"
          value={chosenId}
          placeholder="provider/model-name"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          aria-invalid={candidate !== "" && !valid}
          aria-describedby={error === null ? undefined : errorId}
          onChange={(event) => {
            setChosenId(event.target.value);
            setError(null);
          }}
        />
        {candidate !== "" && !valid && (
          <p className="bj-set-hint">That doesn't look like a model id. Copy it exactly as {providerName} shows it, with no spaces.</p>
        )}
      </div>

      {error !== null && (
        <p id={errorId} className="bj-set-error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="bj-set-status" role="status">
          Checking {candidate} with {providerName}: one tiny real request...
        </p>
      )}
      {done !== null && !busy && error === null && (
        <p className="bj-set-success" role="status">
          <span aria-hidden="true">{"✓"} </span>
          {done} is your default model.
        </p>
      )}

      <div className="bj-set-actions">
        <button type="button" className="bj-set-action" disabled={!valid && !busy} aria-disabled={busy} onClick={() => void choose()}>
          {busy ? "Checking..." : "Use this model"}
        </button>
      </div>
      <p className="bj-set-hint">
        Choosing sends one tiny real request to {providerName} to prove the model works with your key. If it refuses, nothing changes.         choose becomes the default for everything Between Jobs asks a model to do.
      </p>
    </div>
  );
}
