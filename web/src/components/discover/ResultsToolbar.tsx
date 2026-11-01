import { useId } from "react";
import { FILTER_LABEL, SORT_LABEL, countFor, type FitCounts, type FitFilter, type SortMode } from "../../lib/discoverArrange";
import "../../styles/discover.css";

// What a person can do to a page of results: keep one fit (a tier, or the rows that were not scored),
// order them, and open "Why this score" on every row at once. It draws only what the results make
// meaningful -- a fit group when at least two groups hold rows, "Best fit" when two rows carry a number,
// the switch when some shown row has a detail -- and holds no state: ResultsView owns the choices and
// the words said about them.
//
// The groups are the app's own pattern for choosing one of a few things: a labelled group of buttons
// with aria-pressed, on the shared outlined segments, as the "How recent" choice on Hiring signals is
// written. Every control is a native button or checkbox in reading order; there are no arrow-key
// handlers and no shortcuts. The count in a chip is set in mono (it is machine-produced) and the
// accessible name adds the word "results", so the visible label is part of the name.
//
// A press changes the list under the control, so what it did is said once in a polite live region, and
// the visible count line says how many are shown when a fit other than All is chosen.

export interface ResultsToolbarProps {
  counts: FitCounts;
  offeredFilters: readonly FitFilter[];
  filter: FitFilter;
  onFilter: (filter: FitFilter) => void;
  offeredSorts: readonly SortMode[];
  sort: SortMode;
  onSort: (sort: SortMode) => void;
  allOpen: boolean;
  onAllOpen: (open: boolean) => void;
  /** Some shown row has a detail to open. */
  showDetailsSwitch: boolean;
  /** "Showing 6 of 25 results, Strong fit only." for a fit other than All; null otherwise. */
  countLine: string | null;
  /** What the last press did, for the live region; empty until a press. */
  announcement: string;
}

export function ResultsToolbar(props: ResultsToolbarProps) {
  const { counts, offeredFilters, filter, onFilter, offeredSorts, sort, onSort, allOpen, onAllOpen, showDetailsSwitch, countLine, announcement } = props;
  const switchNote = useId();
  return (
    <div className="bj-disc-toolbar">
      <div className="bj-disc-tools">
        {offeredFilters.length > 0 && (
          <div className="bj-disc-tool">
            <span className="bj-disc-tool-label" aria-hidden="true">
              Fit
            </span>
            <div className="bj-segmented bj-disc-chips" role="group" aria-label="Show results by fit">
              {offeredFilters.map((choice) => {
                const count = countFor(counts, choice);
                return (
                  <button key={choice} type="button" className="bj-selectable" aria-pressed={choice === filter} onClick={() => choice !== filter && onFilter(choice)}>
                    {FILTER_LABEL[choice]} <span className="bj-disc-chip-count">{count}</span>
                    <span className="bj-visually-hidden"> {count === 1 ? "result" : "results"}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <div className="bj-disc-tool">
          <span className="bj-disc-tool-label" aria-hidden="true">
            Sort
          </span>
          <div className="bj-segmented bj-disc-sort" role="group" aria-label="Sort results">
            {offeredSorts.map((choice) => (
              <button key={choice} type="button" className="bj-selectable" aria-pressed={choice === sort} onClick={() => choice !== sort && onSort(choice)}>
                {SORT_LABEL[choice]}
              </button>
            ))}
          </div>
        </div>
        {showDetailsSwitch && (
          <>
            <label className="bj-disc-toggle">
              <input type="checkbox" checked={allOpen} aria-describedby={switchNote} onChange={(event) => onAllOpen(event.target.checked)} />
              <span>Show score details</span>
            </label>
            {/* Beside the label, not in it: inside, it would be read as part of the checkbox's name. */}
            <span className="bj-visually-hidden" id={switchNote}>
              Opens Why this score on every row.
            </span>
          </>
        )}
      </div>
      {countLine !== null && <p className="bj-disc-count">{countLine}</p>}
      <p className="bj-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
    </div>
  );
}
