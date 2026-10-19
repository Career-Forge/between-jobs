import { useEffect, useState } from "react";
import { DISCOVER_FORM_ID } from "../../lib/nextStep/pages/discover";
import type { DiscoverRequest } from "../../lib/discoverFacts";
import "../../styles/discover.css";

// The filter form: search words, location, companies and "remote only". It is a real <form>, so Enter in
// any field searches, and the next-step band's Search submits it from outside (the band holds the
// page's one cyan fill, so the form's own Search is drawn outlined, and reads "Search again" once a
// search has run). Nothing here searches on its own.
//
// Busy controls are `aria-disabled`, not `disabled`, so focus stays where it is; the page's handlers
// are the guard. Save this search keeps what the filters hold, for the background checks.

// Roles the search box suggests in turn, so a first-time visitor sees the kinds of words that work.
export const ROLE_PLACEHOLDERS: readonly string[] = ["Data Analyst", "Data Engineer", "Software Development Engineer", "Analytics Engineer", "ML Engineer"];
const ROTATE_MS = 4000;

// The suggestion shown in the empty search box. It changes every few seconds only while the box is
// empty and the person has not asked for less motion; typing stops it, and a person who prefers
// reduced motion gets the first one and no change.
function useRolePlaceholder(active: boolean): string {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (!active) return;
    if (typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const tick = window.setInterval(() => setIndex((current) => (current + 1) % ROLE_PLACEHOLDERS.length), ROTATE_MS);
    return () => window.clearInterval(tick);
  }, [active]);
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
  onSearch: () => void;
  onSave: () => void;
  saving: boolean;
}

export function DiscoverForm({ values, onChange, searching, searched, blockedReason, onSearch, onSave, saving }: DiscoverFormProps) {
  const placeholder = useRolePlaceholder(values.q === "");
  const unavailable = searching || blockedReason !== null;
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
        <button type="submit" aria-disabled={unavailable ? true : undefined} aria-describedby={blockedReason !== null ? "bj-discover-blocked" : undefined}>
          {searching ? "Searching..." : searched ? "Search again" : "Search"}
        </button>
        <button type="button" aria-disabled={saving ? true : undefined} onClick={() => !saving && onSave()}>
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
