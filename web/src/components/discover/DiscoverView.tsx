import { NextStepBand } from "../paper/NextStepBand";
import { PageHeader } from "../paper/PageHeader";
import { EmptyState, FailureState, LoadingState, SetupRequiredState, type StateLink } from "../paper/StateViews";
import type { SavedSearchesActions } from "../SavedSearchesCardView";
import type { DiscoverRequest } from "../../lib/discoverFacts";
import type { DiscoverResponse, JobCard } from "../../lib/discoverTypes";
import type { NextStepOutcome } from "../../lib/nextStep";
import { bandHoldsSearch } from "../../lib/nextStep/pages/discover";
import type { SavedSearchesState } from "../../lib/savedSearches";
import { A_MODEL_CALL, spendNotice } from "../../lib/spendNotice";
import { DiscoverForm } from "./DiscoverForm";
import { ResultsView } from "./ResultsView";
import { SavedSearchesFold } from "./SavedSearchesFold";
import { SearchTimer } from "./SearchTimer";
import "../../styles/discover.css";

// Discover as a function of what the page holds: the header, the next-step band, the filter form, the
// saved searches and the body for each state of a search. Every state is drawn here from props, so each
// can be shown and tested on its own; the reading, the requests and the handlers are pages/Discover.tsx.
//
//   idle      what will appear here, and that nothing runs until a search is asked for
//   loading   static placeholder rows in the row's own shape, with the elapsed counter in the band
//   setup     what is missing, each with a link to where it is fixed -- no request is made
//   error     what could not be read; the band holds "Try again", so the body has no Retry of its own
//   ready     the rows (lib/discoverRow.ts), what did not arrive, "No results" when nothing did; for an
//             answer brought back from an earlier visit, one line under the Results heading says when it
//             is from (ResultsView draws it, inside its own block, so the block's spacing applies to it)

export type SearchView =
  | { kind: "idle" }
  | { kind: "loading"; startedAt: number }
  | { kind: "error"; message: string }
  | { kind: "ready"; result: DiscoverResponse };

export interface SetupView {
  message: string;
  missing: readonly [StateLink, ...StateLink[]];
  /**
   * False when only the server said setup is missing (its own answer to a search, not something the
   * setup reads show): Search stays pressable, since asking the server again is how a person finds out
   * they fixed it elsewhere. True (the default) when the reads show it, where a search could not work.
   */
  blocksSearch?: boolean;
}

export interface DiscoverViewProps {
  now: Date;
  outcome: NextStepOutcome;
  onBandAction: (intent: string) => void;
  /** What a screen reader is told when a search answers or a search is saved. */
  announcement: string;
  values: DiscoverRequest;
  onValuesChange: (values: DiscoverRequest) => void;
  onSearch: () => void;
  onSave: () => void;
  saving: boolean;
  search: SearchView;
  /**
   * Present only while the answer on show was brought back from an earlier visit, never null: `at` is the
   * clock time it arrived, already written for the reader. A fresh answer leaves it out, and so does any
   * state that is not a finished search.
   */
  restored?: { at: string };
  /** Setup is known to be missing (read, or the server said so): drawn instead of the search's body. */
  setup: SetupView | null;
  /** The setup reads are still out: the idle "Find a job" text waits for them, so it is not swapped for the setup block a moment later. */
  checkingSetup?: boolean;
  tracking: ReadonlySet<string>;
  tracked: Readonly<Record<string, string>>;
  trackErrors: Readonly<Record<string, string>>;
  onTrack: (job: JobCard) => void;
  saved: { state: SavedSearchesState; actions: SavedSearchesActions; createError: string | null };
  timeZone?: string;
}

export const SETUP_BLOCKED_NOTE = "Search is waiting on the setup above.";

// What pressing Search costs, said before it is pressed. A search scores what it finds with one batched call
// on the person's own model key, and asks each web-search key they have connected (You.com, Firecrawl, Serper,
// Brave) several times, once per query. It says what a press can do, not what every press does: with no
// search words those four are left alone, and the model call is skipped when no posting survives the link
// checks.
const SEARCH_SPEND = `${A_MODEL_CALL} and can run several searches on each search key you have connected`;

/** The sentence for the form's Search, named as the person reads it: "Search" before one has run, "Search again" after. */
export const searchSpendNote = (press: "Search" | "Search again"): string => spendNotice(press, SEARCH_SPEND);

export const SEARCH_AGAIN_SPEND_NOTE = searchSpendNote("Search again");

export function DiscoverView(props: DiscoverViewProps) {
  const { now, search, setup } = props;
  const searched = search.kind === "ready" || search.kind === "error";
  const blockedReason = setup === null || setup.blocksSearch === false ? null : SETUP_BLOCKED_NOTE;
  // A restored answer's lead (below) already says what Search again costs, and a blocked search is not
  // one anyone can press, so the form says nothing in either case.
  const spendNote = blockedReason !== null || props.restored !== undefined ? null : searchSpendNote(searched ? "Search again" : "Search");
  return (
    <div className="bj-discover">
      <div className="bj-visually-hidden" role="status" aria-live="polite">
        {props.announcement}
      </div>
      <PageHeader title="Discover" subtitle="Company job boards and your search providers, scored against your profile by your own model." />
      <NextStepBand outcome={props.outcome} onAction={props.onBandAction}>
        {search.kind === "loading" && <SearchTimer startedAt={search.startedAt} />}
      </NextStepBand>
      <DiscoverForm
        values={props.values}
        onChange={props.onValuesChange}
        searching={search.kind === "loading"}
        searched={searched}
        blockedReason={blockedReason}
        spendNote={spendNote}
        searchInBand={bandHoldsSearch(props.outcome)}
        bandPending={props.outcome.status === "loading"}
        onSearch={props.onSearch}
        onSave={props.onSave}
        saving={props.saving}
      />
      <SavedSearchesFold saved={props.saved} saving={props.saving} now={now} timeZone={props.timeZone} />
      {setup !== null ? (
        <SetupRequiredState message={setup.message} missing={setup.missing} />
      ) : (
        <>
          {search.kind === "idle" && props.checkingSetup !== true && (
            <EmptyState heading="Find a job">
              <p>
                Postings from company job boards and your connected search providers appear here, each with a fit score from your model. Nothing is searched until you ask: enter words
                above, or leave the filters empty to browse the latest postings.
              </p>
            </EmptyState>
          )}
          {search.kind === "loading" && <LoadingState rows={3} rowHeight={200} label="Searching..." announce={false} />}
          {search.kind === "error" && <FailureState message={`The search couldn't be read: ${search.message}`} />}
          {search.kind === "ready" && (
            <ResultsView
              result={search.result}
              now={now}
              tracking={props.tracking}
              tracked={props.tracked}
              trackErrors={props.trackErrors}
              onTrack={props.onTrack}
              lead={
                props.restored !== undefined ? (
                  <p className="bj-disc-note">
                    Showing your search from {props.restored.at}. The scores and link checks are from then and have not been refreshed. {SEARCH_AGAIN_SPEND_NOTE}
                  </p>
                ) : undefined
              }
              timeZone={props.timeZone}
            />
          )}
        </>
      )}
    </div>
  );
}
