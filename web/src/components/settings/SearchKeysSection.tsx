import { useId, useRef, useState } from "react";
import { apiFetch } from "../../lib/api";
import { SEARCH_PROVIDER_CARDS, cardNote, providerNotice, type SearchProviderCardSpec } from "../../lib/providerStatus";
import { formatTrailDate, mono } from "../../lib/trail";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { settingsFailureMessage } from "../../lib/settings/settingsErrors";
import { StaticTrailLine } from "../paper/TrailLine";
import { KeyField } from "./KeyField";
import { RemoveKey } from "./RemoveKey";
import "../../styles/settings.css";

// "Search and data keys": the optional keys for Discover's job search, contact enrichment and
// company research. Each is a row with a state word, what the key is for (and what saving it
// costs), whether the integration has ever been seen working against the real service, and, for a
// key that is saved, one trail line: who checked it and when.
//
// A key is not set, or it is connected; there is no third state and no half-way "configured". "Not
// set" is the words alone: a key that was never saved produced nothing, so it has no trail and no
// glyph. The rows are drawn from SEARCH_PROVIDER_CARDS (lib/providerStatus.ts), never typed out
// per provider.
//
// The Brave and Serper notes mention hiring posts ONLY for a person Hiring signals is enabled for
// (cardNote): for anyone else the server treats the feature as not existing, so the page must not
// say their key is used for it.

export interface SearchKeyRowProps {
  card: SearchProviderCardSpec;
  /** Hiring signals is on for this person (see the note above). */
  hiringSignalsEnabled: boolean;
  credential: CredentialSummary | null;
  now: Date;
  onChanged: () => Promise<void>;
}

export function SearchKeyRow({ card, hiringSignalsEnabled, credential, now, onChanged }: SearchKeyRowProps) {
  const { title, provider, placeholder } = card;
  const note = cardNote(card, hiringSignalsEnabled);
  // Whether this provider has had a successful run against the real service (lib/providerStatus.ts).
  const notice = providerNotice(provider);
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const errorId = useId();
  const when = credential === null ? null : formatTrailDate(credential.updated_at, { now });

  async function save() {
    if (saving || secret.trim() === "") return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/credentials", { method: "POST", body: JSON.stringify({ service: "search", provider, secret }) });
      // Stored: the key leaves the page now.
      setSecret("");
      setAdding(false);
      await onChanged();
      opener.current?.focus();
    } catch (e) {
      setError(settingsFailureMessage(e, { action: "save_key", provider: title }, "Failed to save that key."));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setRemoving(true);
    setError(null);
    try {
      await apiFetch(`/credentials/search/${provider}`, { method: "DELETE" });
      await onChanged();
    } catch (e) {
      setError(settingsFailureMessage(e, { action: "remove_key", provider: title }, "Failed to remove that key."));
    } finally {
      setRemoving(false);
    }
  }

  return (
    <li className="bj-set-item" id={`search-${provider}`}>
      <div className="bj-set-row">
        <div className="bj-set-row-main">
          <div className="bj-set-row-title">
            <h3>{title}</h3>
            {credential !== null ? <span className="bj-badge-emerald">{"✓"} Connected</span> : <span className="bj-badge-muted">Not set</span>}
          </div>
          <p className="bj-set-note">{note}</p>
          {notice !== null && (
            <p className="bj-set-hint">
              <span className="bj-badge-muted">{notice.label}</span> {notice.detail}
              {notice.help !== null && ` ${notice.help}`}
            </p>
          )}
          {credential !== null && (
            <StaticTrailLine trail={{ glyph: "code", actor: "Checked by code on save", basis: card.checkNote, when: when === null ? null : mono(when) }} />
          )}
        </div>
        <div className="bj-set-row-actions">
          <button ref={opener} type="button" aria-expanded={adding} onClick={() => setAdding((open) => !open)}>
            {credential === null ? "Add key" : "Replace key"}
          </button>
          <a href={card.getKeyUrl} target="_blank" rel="noopener noreferrer">
            {credential === null ? "Get a key" : "Open key page"}
            <span className="bj-visually-hidden"> at {title} (opens in a new tab)</span>
          </a>
          {credential !== null && <RemoveKey what={`${title} key`} holder={title} busy={removing} onRemove={() => void remove()} />}
        </div>
      </div>
      {adding && (
        <form
          className="bj-set-card bj-set-inline-form"
          method="post"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <KeyField
            label={credential === null ? `${title} API key` : `New ${title} API key`}
            value={secret}
            onChange={setSecret}
            placeholder={placeholder}
            disabled={saving}
            describedBy={error === null ? undefined : errorId}
          />
          <div className="bj-set-actions">
            <button type="submit" className="bj-set-action" disabled={secret.trim() === "" && !saving} aria-disabled={saving}>
              {saving ? "Validating..." : "Validate and save"}
            </button>
            <button
              type="button"
              className="bj-quiet"
              onClick={() => {
                setAdding(false);
                setSecret("");
                setError(null);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {error !== null && (
        <p id={errorId} className="bj-set-error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

export interface SearchKeysSectionProps {
  credentials: readonly CredentialSummary[];
  hiringSignalsEnabled: boolean;
  now: Date;
  onChanged: () => Promise<void>;
}

export function SearchKeysSection({ credentials, hiringSignalsEnabled, now, onChanged }: SearchKeysSectionProps) {
  const rows = SEARCH_PROVIDER_CARDS.map((card) => ({
    card,
    credential: credentials.find((row) => row.service === "search" && row.provider === card.provider) ?? null,
  }));
  const connected = rows.filter((row) => row.credential !== null).length;
  return (
    <section id="search-keys" className="bj-set-section" aria-labelledby="search-keys-h">
      <div className="bj-set-section-head">
        <h2 id="search-keys-h" tabIndex={-1}>
          Search and data keys
        </h2>
        <p className="bj-set-count">
          {connected} of {rows.length} connected
        </p>
      </div>
      <p className="bj-set-lede">
        Optional. Each key turns on a part of Discover or of contact research, and each note says what saving the key costs. Nothing here is used until
        you save a key.
      </p>
      <ul className="bj-set-list" aria-label="Search and data keys">
        {rows.map(({ card, credential }) => (
          <SearchKeyRow key={card.provider} card={card} hiringSignalsEnabled={hiringSignalsEnabled} credential={credential} now={now} onChanged={onChanged} />
        ))}
      </ul>
    </section>
  );
}
