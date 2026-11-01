import { useEffect, useState } from "react";
import { DISCOVER_FORM_ID } from "../../lib/nextStep/pages/discover";
import type { DiscoverRequest } from "../../lib/discoverFacts";
import "../../styles/discover.css";

// The filter form: search words, location, companies and "remote only". It is a real <form>, so Enter in
// any field searches. The next-step band's Search submits it from outside (the band holds the page's one
// cyan fill), and until a search has run the band's is the only Search on the page: the form draws none
// of its own, since two buttons that say Search a few lines apart read as two different things. Enter
// still works, because a form's default button is the first submit button whose form owner is the form,
// and a button attached by its `form` attribute counts. Once a search has run the form keeps its own
// outlined "Search again" in every state, so the button a person pressed is never taken from under them.
// Nothing here searches on its own.
//
// Busy controls are `aria-disabled`, not `disabled`, so focus stays where it is; the page's handlers
// are the guard. Save this search keeps what the filters hold, for the background checks; it is a quiet
// button, since saving is the lower-stakes action.

// Roles the search box suggests in turn, so a first-time visitor sees the kinds of words that work.
export const ROLE_PLACEHOLDERS: readonly string[] = ["Data Analyst", "Data Engineer", "Software Development Engineer", "Analytics Engineer", "ML Engineer"];
const ROTATE_MS = 4000;

// The suggestion shown in the empty search box. It changes every few seconds, once through the list,
// and only while the box is empty and not focused (a person who has gone to the field is reading the
// suggestion, and a moving one is a distraction; leaving it empty resumes where it was). Typing stops
// it, it stops on the last suggestion instead of looping forever, and a person who prefers reduced
// motion gets the first one and no change (WCAG 2.2.2: nothing here moves on its own for good).
function useRolePlaceholder(active: boolean): string {
  const [index, setIndex] = useState(0);
  const last = ROLE_PLACEHOLDERS.length - 1;
  useEffect(() => {
    if (!active || index >= last) return;
    if (typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const tick = window.setTimeout(() => setIndex((current) => Math.min(current + 1, last)), ROTATE_MS);
    return () => window.clearTimeout(tick);
  }, [active, index, last]);
  return `e.g. ${ROLE_PLACEHOLDERS[index]}`;
}

export interface DiscoverFormProps {
  values: DiscoverRequest;
  onChange: (values: DiscoverRequest) => void;
  /** A search is in flight: Search reads "Searching..." and ignores presses. */
  searching: boolean;
  /** A search has run on this visit: the button says "Search again". */
  searched: boolean;
  /** Setup is known to be missing: Search ignores presses, and the reason is said beside it. */
  blockedReason: string | null;
  /** The next-step band's button is this form's Search (a submit that names it). */
  searchInBand: boolean;
  /** The band has not decided yet (it is loading), so the form does not draw a Search that it would take away a moment later. */
  bandPending: boolean;
  onSearch: () => void;
  onSave: () => void;
  saving: boolean;
}

export function DiscoverForm({ values, onChange, searching, searched, blockedReason, searchInBand, bandPending, onSearch, onSave, saving }: DiscoverFormProps) {
  const [searchFocused, setSearchFocused] = useState(false);
  const placeholder = useRolePlaceholder(values.q === "" && !searchFocused);
  const unavailable = searching || blockedReason !== null;
  // The form's own Search is left out only before the first search of the visit, and only while the
  // band holds it or has not yet said. The blocked note hangs on the form's button, so it is drawn
  // whenever setup is blocked.
  const showSubmit = searched || searching || blockedReason !== null || !(searchInBand || bandPending);
  return (
    <form
      id={DISCOVER_FORM_ID}
      className="bj-disc-form"
      aria-label="Search filters"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (!searching) onSearch();
      }}
    >
      <div className="bj-disc-fields">
        <label className="bj-field bj-disc-field-wide">
          <span>Search</span>
          <input
            type="text"
            value={values.q}
            maxLength={200}
            placeholder={placeholder}
            autoComplete="off"
            onFocus={() => setSearchFocused(true)}
            onBlur={() => setSearchFocused(false)}
            onChange={(event) => onChange({ ...values, q: event.target.value })}
          />
        </label>
        <label className="bj-field">
          <span>Location</span>
          <input
            type="text"
            value={values.location}
            maxLength={100}
            placeholder="e.g. New York, NY"
            autoComplete="off"
            onChange={(event) => onChange({ ...values, location: event.target.value })}
          />
        </label>
        <label className="bj-field">
          <span>Companies</span>
          <input
            type="text"
            value={values.companies}
            placeholder="e.g. Anthropic, Stripe"
            autoComplete="off"
            onChange={(event) => onChange({ ...values, companies: event.target.value })}
          />
        </label>
      </div>
      <label className="bj-disc-remote">
        <input type="checkbox" checked={values.remoteOnly} onChange={(event) => onChange({ ...values, remoteOnly: event.target.checked })} />
        <span>Remote only</span>
      </label>
      <div className="bj-disc-form-actions">
        {showSubmit && (
          <button type="submit" aria-disabled={unavailable ? true : undefined} aria-describedby={blockedReason !== null ? "bj-discover-blocked" : undefined}>
            {searching ? "Searching..." : searched ? "Search again" : "Search"}
          </button>
        )}
        <button type="button" className="bj-quiet" aria-disabled={saving ? true : undefined} onClick={() => !saving && onSave()}>
          {saving ? "Saving..." : "Save this search"}
        </button>
      </div>
      {blockedReason !== null && (
        <p id="bj-discover-blocked" className="bj-disc-note">
          {blockedReason}
        </p>
      )}
    </form>
  );
}
