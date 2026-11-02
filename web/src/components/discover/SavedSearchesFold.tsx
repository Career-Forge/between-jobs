import { useId, useState } from "react";
import { savedSearchesSummary } from "../../lib/discoverSaved";
import { SAVED_SEARCHES_HEADING } from "../../lib/savedSearches";
import { SavedSearchesCardView } from "../SavedSearchesCardView";
import type { DiscoverViewProps } from "./DiscoverView";
import "../../styles/discover.css";

// The saved searches on Discover, folded into one row so they do not push the results down the page.
// The row is the section's heading and its button: "Saved searches", one hint that says how many and
// whether they are running, and a glyph for open or closed. Opening it shows the card as it is on
// Settings, minus its own heading (this row is the heading).
//
// A closed row must not hide a failure. A failed read, a refused save or an update that did not go
// through opens the row on its own (on the first draw, with no flash of the closed row, and whenever
// one turns up later), and the closed row's hint names the failure as well. After that the row is the
// person's: the button always works, and what they closed stays closed. A request that is merely under
// way ("Updating...", "Saving...") shows in the hint and opens nothing, and so does a save that has
// landed: the new search is not on screen while the row is closed, so the hint says "Search saved".
//
// With no saved searches, or none known yet, there is no row, since there is nothing to fold away.
// A new visit starts closed.

export interface SavedSearchesFoldProps {
  saved: DiscoverViewProps["saved"];
  /** A save is out: the hint says so while there are searches to show. */
  saving: boolean;
  now: Date;
  timeZone?: string;
}

export function SavedSearchesFold({ saved, saving, now, timeZone }: SavedSearchesFoldProps) {
  // A save that ends without an error has worked, and a closed row is the only place a person who did not
  // open it can see that. Derived while rendering, so the news is there on the first draw after the save.
  // It lasts until the person opens or closes the row, or the next save starts.
  const [wasSaving, setWasSaving] = useState(saving);
  const [justSaved, setJustSaved] = useState(false);
  if (saving !== wasSaving) {
    setWasSaving(saving);
    setJustSaved(!saving && saved.createError === null);
  }
  const summary = savedSearchesSummary({ state: saved.state, createError: saved.createError, saving, justSaved });
  const attention = summary?.attention === true;
  const [open, setOpen] = useState(attention);
  // Derived while rendering, so the row is never drawn closed over a failure that has just appeared:
  // attention turning on opens it. A list known to be empty (every search deleted) has no row, and a
  // row that comes back later starts closed; a list that is only being read again keeps what it was.
  const [wasAttention, setWasAttention] = useState(attention);
  if (attention !== wasAttention) {
    setWasAttention(attention);
    if (attention) setOpen(true);
  }
  if (summary === null && saved.state.kind === "ready" && open) setOpen(false);
  const bodyId = useId();
  if (summary === null) return null;
  return (
    <div className="bj-disc-saved">
      <h2 className="bj-disc-saved-heading">
        <button
          type="button"
          className="bj-disc-saved-toggle"
          aria-expanded={open}
          aria-controls={open ? bodyId : undefined}
          onClick={() => {
            setOpen(!open);
            setJustSaved(false);
          }}
        >
          <span className="bj-disc-saved-title">{SAVED_SEARCHES_HEADING}</span>{" "}
          <span className="bj-disc-saved-hint">{summary.hint}</span>{" "}
          <span className="bj-disc-saved-glyph" aria-hidden="true">
            {open ? "⌃" : "›"}
          </span>
        </button>
      </h2>
      {open && (
        <div className="bj-disc-saved-body" id={bodyId}>
          <SavedSearchesCardView state={saved.state} actions={saved.actions} now={now} createError={saved.createError} timeZone={timeZone} headingless />
        </div>
      )}
    </div>
  );
}
