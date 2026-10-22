import { useId, useRef, useState } from "react";
import { apiFetch } from "../../lib/api";
import {
  HIRING_POSTS_TITLE,
  SEARCH_GROUPS,
  SEARCH_PROVIDER_CARDS,
  cardNote,
  hiringPostsLine,
  providerNotice,
  type SearchProviderCardSpec,
} from "../../lib/providerStatus";
import { formatTrailDate, mono } from "../../lib/trail";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { settingsFailureMessage } from "../../lib/settings/settingsErrors";
import { StaticTrailLine } from "../paper/TrailLine";
import { KeyField } from "./KeyField";
import { RemoveKey } from "./RemoveKey";
import "../../styles/settings.css";

// "Optional extras": the optional keys for live job search, finding people and, for those who have
// it, hiring posts. ONE disclosure, closed until someone opens it (or arrives by a link that names it:
// /settings#search-keys, or one key's row, #search-brave), so a person who needs none of them is not
// shown seven.
//
// The rows are grouped by what the key is for (SEARCH_GROUPS in lib/providerStatus.ts, which says how
// each provider was placed). A connected key is one quiet line with Replace key and Remove key and a
// trail line (who checked it and when); a key that is not connected is its name and a Connect text
// action that opens the form. The form says what the key is for and what saving it costs, whether the
// integration has ever been seen working against the real service, and takes the key. There is no
// count of connected keys anywhere: nothing here is part of setup, and nothing waits on it.
//
// The element keeps the id `search-keys` (and its heading `search-keys-h`) because the API and the
// pages that send people here (Discover, Hiring signals) already link to /settings#search-keys.
//
// A key is not set, or it is connected; there is no third state and no half-way "configured". "Not
// set" is the absence of the Connected words: a key that was never saved produced nothing, so it has
// no trail and no glyph. The rows are drawn from SEARCH_PROVIDER_CARDS, never typed out per provider.
//
// The Brave, Serper, You.com and Firecrawl notes mention hiring posts ONLY for a person Hiring signals
// is enabled for (cardNote), and so does the "Hiring posts" line: for anyone else the server treats
// the feature as not existing, so the page must not say their key is used for it.

export interface SearchKeyRowProps {
  card: SearchProviderCardSpec;
  /** Hiring signals is on for this person (see the note above). */
  hiringSignalsEnabled: boolean;
  credential: CredentialSummary | null;
  now: Date;
  onChanged: () => Promise<void>;
  /** Draw the key form from the start (a static render of the form has no click to open it). */
  initiallyOpen?: boolean;
}

export function SearchKeyRow({ card, hiringSignalsEnabled, credential, now, onChanged, initiallyOpen = false }: SearchKeyRowProps) {
  const { title, provider, placeholder } = card;
  const note = cardNote(card, hiringSignalsEnabled);
  // Whether this provider has had a successful run against the real service (lib/providerStatus.ts).
  const notice = providerNotice(provider);
  const [adding, setAdding] = useState(initiallyOpen);
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
      // The confirmation that had focus is gone. The row's own button (now "Connect") is the nearest
      // place in the same list, so the keyboard stays here instead of falling to the top of the page.
      opener.current?.focus();
    } catch (e) {
      setError(settingsFailureMessage(e, { action: "remove_key", provider: title }, "Failed to remove that key."));
    } finally {
      setRemoving(false);
    }
  }

  const connected = credential !== null;
  const formId = useId();
  return (
    <li className="bj-set-extra" id={`search-${provider}`}>
      <div className="bj-set-extra-line">
        {/* tabIndex -1: a link to this row (#search-<provider>) leaves the keyboard here (lib/useScrollToHash.ts). */}
        <h4 className="bj-set-extra-name" tabIndex={-1}>
          {title}
        </h4>
        {connected && (
          <span className="bj-set-extra-state">
            <span className="bj-set-extra-glyph" aria-hidden="true">
              {"✓"}
            </span>{" "}
            Connected
          </span>
        )}
        <div className="bj-set-extra-actions">
          <button
            ref={opener}
            type="button"
            className="bj-quiet"
            aria-expanded={adding}
            aria-controls={adding ? formId : undefined}
            onClick={() => setAdding((open) => !open)}
          >
            {connected ? (
              <>
                Replace key<span className="bj-visually-hidden"> for {title}</span>
              </>
            ) : (
              <>
                Connect<span className="bj-visually-hidden"> {title}</span>
              </>
            )}
          </button>
          {connected && <RemoveKey what={`${title} key`} holder={title} subject={title} busy={removing} onRemove={() => void remove()} />}
        </div>
      </div>
      {connected && (
        <StaticTrailLine trail={{ glyph: "code", actor: "Checked by code on save", basis: card.checkNote, when: when === null ? null : mono(when) }} />
      )}
      {connected && notice !== null && (
        <p className="bj-set-hint">
          <span className="bj-badge-muted">{notice.label}</span> {notice.detail}
          {notice.help !== null && ` ${notice.help}`}
        </p>
      )}
      {adding && (
        <form
          id={formId}
          className="bj-set-card bj-set-inline-form"
          method="post"
          noValidate
          aria-label={connected ? `Replace your ${title} key` : `Connect ${title}`}
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <p className="bj-set-note">{note}</p>
          {!connected && notice !== null && (
            <p className="bj-set-hint">
              <span className="bj-badge-muted">{notice.label}</span> {notice.detail}
              {notice.help !== null && ` ${notice.help}`}
            </p>
          )}
          <a className="bj-set-getkey" href={card.getKeyUrl} target="_blank" rel="noopener noreferrer">
            Get a key at {title}
            <span className="bj-visually-hidden"> (opens in a new tab)</span>
          </a>
          <KeyField
            label={connected ? `New ${title} API key` : `${title} API key`}
            value={secret}
            onChange={setSecret}
            placeholder={placeholder}
            readOnly={saving}
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
                opener.current?.focus();
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
  /** Whether the disclosure is open. The page holds it so a link to a key inside can open it. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SearchKeysSection({ credentials, hiringSignalsEnabled, now, onChanged, open, onOpenChange }: SearchKeysSectionProps) {
  const bodyId = useId();
  return (
    <section id="search-keys" className="bj-set-section bj-extras" aria-labelledby="search-keys-h">
      <h2 id="search-keys-h" tabIndex={-1}>
        <button type="button" className="bj-extras-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => onOpenChange(!open)}>
          Optional extras
          <span className="bj-extras-chevron" aria-hidden="true">
            {"›"}
          </span>
        </button>
      </h2>
      <p className="bj-set-lede">
        Discover works without these. They add {hiringSignalsEnabled ? "live job search, company and event research, people lookup and hiring posts" : "live job search, company and event research, and people lookup"}.
      </p>
      {/* Always drawn and hidden while closed, so a link to one key (or a form half-filled) is not lost by closing. */}
      <div id={bodyId} className="bj-extras-body" hidden={!open}>
        {SEARCH_GROUPS.map((group) => (
          <section key={group.id} className="bj-set-group" aria-labelledby={`${bodyId}-${group.id}`}>
            <h3 id={`${bodyId}-${group.id}`} className="bj-set-group-title">
              {group.title}
            </h3>
            <ul className="bj-set-extras" aria-label={group.title}>
              {SEARCH_PROVIDER_CARDS.filter((card) => card.group === group.id).map((card) => (
                <SearchKeyRow
                  key={card.provider}
                  card={card}
                  hiringSignalsEnabled={hiringSignalsEnabled}
                  credential={credentials.find((row) => row.service === "search" && row.provider === card.provider) ?? null}
                  now={now}
                  onChanged={onChanged}
                />
              ))}
            </ul>
          </section>
        ))}
        {hiringSignalsEnabled && (
          <section className="bj-set-group" aria-labelledby={`${bodyId}-hiring`}>
            <h3 id={`${bodyId}-hiring`} className="bj-set-group-title">
              {HIRING_POSTS_TITLE}
            </h3>
            <p className="bj-set-hint">{hiringPostsLine()}</p>
          </section>
        )}
      </div>
    </section>
  );
}
