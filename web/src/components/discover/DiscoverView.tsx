import { NextStepBand } from "../paper/NextStepBand";
import { PageHeader } from "../paper/PageHeader";
import { EmptyState, FailureState, LoadingState, SetupRequiredState, type StateLink } from "../paper/StateViews";
import { SavedSearchesCardView, type SavedSearchesActions } from "../SavedSearchesCardView";
import type { DiscoverRequest } from "../../lib/discoverFacts";
import type { DiscoverResponse, JobCard } from "../../lib/discoverTypes";
import type { NextStepOutcome } from "../../lib/nextStep";
import type { SavedSearchesState } from "../../lib/savedSearches";
import { DiscoverForm } from "./DiscoverForm";
import { ResultsView } from "./ResultsView";
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
//   ready     the rows (lib/discoverRow.ts), what did not arrive, "No results" when nothing did

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

export function DiscoverView(props: DiscoverViewProps) {
  const { now, search, setup } = props;
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
        searched={search.kind === "ready" || search.kind === "error"}
        blockedReason={setup === null || setup.blocksSearch === false ? null : SETUP_BLOCKED_NOTE}
        onSearch={props.onSearch}
        onSave={props.onSave}
        saving={props.saving}
      />
      <SavedSearchesCardView state={props.saved.state} actions={props.saved.actions} now={now} createError={props.saved.createError} timeZone={props.timeZone} />
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
          {search.kind === "loading" && <LoadingState rows={3} rowHeight={168} label="Searching..." />}
          {search.kind === "error" && <FailureState message={`The search couldn't be read: ${search.message}`} />}
          {search.kind === "ready" && (
            <ResultsView
              result={search.result}
              now={now}
              tracking={props.tracking}
              tracked={props.tracked}
              trackErrors={props.trackErrors}
              onTrack={props.onTrack}
              timeZone={props.timeZone}
            />
          )}
        </>
      )}
    </div>
  );
}
