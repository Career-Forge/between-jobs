import { useEffect, useId, useState } from "react";
import { DISCOVER_FORM_ID } from "../../lib/nextStep/pages/discover";
import type { DiscoverRequest } from "../../lib/discoverFacts";
import { A_MODEL_CALL } from "../../lib/spendNotice";
import "../../styles/discover.css";

// The filter form: search words, location, companies and "remote only". It is a real <form>, so Enter in
// any field searches. The next-step band's Search submits it from outside (the band holds the page's one
// gold fill), and until a search has run the band's is the only Search on the page: the form draws none
// of its own, since two buttons that say Search a few lines apart read as two different things. Enter
// still works, because a form's default button is the first submit button whose form owner is the form,
// and a button attached by its `form` attribute counts. Once a search has run the form keeps its own
// outlined "Search again" in every state, so the button a person pressed is never taken from under them.
// Nothing here searches on its own.
//
// Under the buttons the form says what pressing Search costs (the sentence comes in as `spendNote`). The band's
// Search and Search again submit this form and its Try again re-runs the same search, so the one line sits
// beside all of them. It is left out while setup blocks the search and while a restored answer's own line is
// saying the same thing.
//
// The sentence is the Search button's description (`aria-describedby`), so a keyboard or screen reader user who
// reaches the button hears the cost with its name. The note carries an id, given by the page because the band's
// Search points at the same note, and the button names it only while a note is on the page: here under the
// buttons, or, for a restored answer, in the line above the results (`spendNoteInLead`).
//
// Busy controls are `aria-disabled`, not `disabled`, so focus stays where it is; the page's handlers
// are the guard. Save this search keeps what the filters hold, for the background checks; it is a quiet
// button, since saving is the lower-stakes action.

// What pressing Save this search starts. The press itself only writes a row; a background check then runs over
// every active saved search every few hours, and scores a search's new postings with one batched call on the
// person's own model key, only when it finds some. Pausing or deleting the search stops the checks. It is
// drawn whenever the button is, so the form reads the same for a fresh answer and a restored one.
export const SAVE_SEARCH_SPEND_NOTE = `Pressing Save this search starts background checks. Each time one finds new postings, scoring them makes ${A_MODEL_CALL}, until you pause or delete the search.`;

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
  /** A finished search is on show, run on this visit or brought back from an earlier one: the button says "Search again". */
  searched: boolean;
  /** Setup is known to be missing: Search ignores presses, and the reason is said beside it. */
  blockedReason: string | null;
  /**
   * What pressing Search costs, said under the buttons. The band's Search, Search again and Try again make the
   * same request, so the one sentence covers them. Null when it is said somewhere else or does not apply.
   */
  spendNote: string | null;
  /**
   * The id the cost note carries, wherever it is drawn: under the buttons, or in the restored answer's lead. The
   * button is described by it only while one of the two is on the page. The form makes its own when none is given.
   */
  spendNoteId?: string;
  /** The restored answer's lead draws the cost note, so the form does not: the button is described by that one. */
  spendNoteInLead?: boolean;
  /** The next-step band's button is this form's Search (a submit that names it). */
  searchInBand: boolean;
  /** The band has not decided yet (it is loading), so the form does not draw a Search that it would take away a moment later. */
  bandPending: boolean;
  onSearch: () => void;
  onSave: () => void;
  saving: boolean;
}

export function DiscoverForm({ values, onChange, searching, searched, blockedReason, spendNote, spendNoteId, spendNoteInLead = false, searchInBand, bandPending, onSearch, onSave, saving }: DiscoverFormProps) {
  const ownNoteId = useId();
  const noteId = spendNoteId ?? ownNoteId;
  const describedBy = [spendNote !== null || spendNoteInLead ? noteId : null, blockedReason !== null ? "bj-discover-blocked" : null].filter((id) => id !== null).join(" ");
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
          <button type="submit" aria-disabled={unavailable ? true : undefined} aria-describedby={describedBy === "" ? undefined : describedBy}>
            {searching ? "Searching..." : searched ? "Search again" : "Search"}
          </button>
        )}
        <button type="button" className="bj-quiet" aria-disabled={saving ? true : undefined} onClick={() => !saving && onSave()}>
          {saving ? "Saving..." : "Save this search"}
        </button>
      </div>
      {spendNote !== null && (
        <p id={noteId} className="bj-muted bj-small">
          {spendNote}
        </p>
      )}
      <p className="bj-muted bj-small">{SAVE_SEARCH_SPEND_NOTE}</p>
      {blockedReason !== null && (
        <p id="bj-discover-blocked" className="bj-disc-note">
          {blockedReason}
        </p>
      )}
    </form>
  );
}
