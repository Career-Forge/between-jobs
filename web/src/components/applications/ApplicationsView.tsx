import type { Application, ApplicationStatus } from "../../lib/applicationsTypes";
import type { NextStepOutcome } from "../../lib/nextStep";
import ApplicationsBoard from "../ApplicationsBoard";
import { ViewToggle } from "../ViewToggle";
import { NextStepBand } from "../paper/NextStepBand";
import { PageHeader } from "../paper/PageHeader";
import { EmptyState, FailureState, LoadingState } from "../paper/StateViews";
import { ApplicationListCard } from "./ApplicationListCard";
import type { MoveProblem } from "./ApplicationParts";
import { StatusPicker } from "./StatusPicker";
import { PasteJobForm, UrlIngestForm } from "./TrackForms";
import "../../styles/applications.css";

// The Applications screen as a function of what it has read: the header, the next-step band, then
// the list (placeholders while it is read, the failure that stopped it, the teaching for an empty
// list, or the cards as a list, a board or -- on a phone -- one status at a time), then the two ways
// to start tracking a job. Every state is drawn here from props, so each can be shown and tested on
// its own; the reading, the requests and the handlers are pages/Applications.tsx.
//
// The tracking forms come after the list in every state, so they do not jump from above the list to
// below it when it arrives. With nothing tracked yet the list is a short note and the forms are
// right under it.

export type ApplicationsListState =
  | { kind: "loading" }
  /** The first read failed: there is nothing to show. The band holds "Try again". */
  | { kind: "failed"; message: string }
  /** `refreshFailed`: a later read failed, so the cards shown are the last ones that were read. */
  | { kind: "ready"; applications: readonly Application[]; refreshFailed: string | null };

export type ApplicationsLayout = "list" | "board";

export interface ApplicationsViewProps {
  now: Date;
  outcome: NextStepOutcome;
  onBandAction: (intent: string) => void;
  list: ApplicationsListState;
  /** Under 960px: one status at a time, and no List/Board toggle. */
  phone: boolean;
  layout: ApplicationsLayout;
  onLayoutChange: (layout: ApplicationsLayout) => void;
  /** The status the phone list is showing. */
  phoneStatus: ApplicationStatus;
  onPhoneStatusChange: (status: ApplicationStatus) => void;
  busyIds: ReadonlySet<string>;
  problems: Readonly<Record<string, MoveProblem | undefined>>;
  onChangeStatus: (applicationId: string, status: ApplicationStatus) => void;
  onRetryMove: (applicationId: string) => void;
  /** The tracking forms' own requests finished: read the list again. */
  onCreated: () => Promise<void>;
  /** Read the list again: the body callout's own Retry, offered only where the band holds none. */
  onRetryList: () => void;
  /** The header's "Track a job": scroll to the paste form. */
  onTrack: () => void;
  /** What a screen reader hears after a move: the polite status the page speaks through. */
  announcement: string;
}

// Whether the band already holds a button that reads the list again (F1's Try again, or G1's, which
// reads the list too). When a setup row (G2, G3) holds the band instead, the failed list read has no
// way to be retried unless the body offers one.
function bandRetriesTheList(outcome: NextStepOutcome): boolean {
  if (outcome.status !== "ready") return false;
  const action = outcome.step.action;
  return action !== null && action.kind === "button" && (action.intent === "retry" || action.intent === "retry-setup");
}

const TRACK_HEADING_ID = "bj-app-track-heading";
const LIST_HEADING_ID = "bj-app-list-heading";

function Cards({ props, applications }: { props: ApplicationsViewProps; applications: readonly Application[] }) {
  if (props.phone) {
    return (
      <StatusPicker
        applications={applications}
        now={props.now}
        status={props.phoneStatus}
        onStatusChange={props.onPhoneStatusChange}
        busyIds={props.busyIds}
        problems={props.problems}
        onChangeStatus={props.onChangeStatus}
        onRetry={props.onRetryMove}
      />
    );
  }
  if (props.layout === "board") {
    return (
      <ApplicationsBoard
        applications={applications}
        now={props.now}
        busyIds={props.busyIds}
        problems={props.problems}
        onChangeStatus={props.onChangeStatus}
        onRetry={props.onRetryMove}
      />
    );
  }
  return (
    <ul className="bj-app-list" aria-labelledby={LIST_HEADING_ID}>
      {applications.map((application) => (
        <ApplicationListCard
          key={application.id}
          application={application}
          now={props.now}
          busy={props.busyIds.has(application.id)}
          problem={props.problems[application.id]}
          onChangeStatus={(status) => props.onChangeStatus(application.id, status)}
          onRetry={() => props.onRetryMove(application.id)}
        />
      ))}
    </ul>
  );
}

function ListBody(props: ApplicationsViewProps) {
  const { list } = props;
  const onRetry = bandRetriesTheList(props.outcome) ? undefined : props.onRetryList;
  if (list.kind === "loading") {
    return <LoadingState rows={3} rowHeight={132} label="Loading your applications..." />;
  }
  if (list.kind === "failed") {
    return <FailureState message={`Your applications couldn't be read: ${list.message}`} onRetry={onRetry} />;
  }
  if (list.applications.length === 0) {
    // An empty list that could not be read again may be out of date (a job may have just been
    // saved), so it is not taught as "nothing tracked yet" until a read says so.
    if (list.refreshFailed !== null) {
      return <FailureState message={`Your applications couldn't be read again, so the list may be out of date: ${list.refreshFailed}`} onRetry={onRetry} />;
    }
    return (
      <>
        <EmptyState heading="Nothing tracked yet">
          <p>
            Each job you track lands here as one card with its status, and as {"“"}Resume drafted{"”"} once a resume draft exists for it. There are
            two ways in: paste a posting, or fetch one by its URL.
          </p>
        </EmptyState>
      </>
    );
  }
  const count = list.applications.length;
  return (
    <section className="bj-app-section" aria-labelledby={LIST_HEADING_ID}>
      {list.refreshFailed !== null && (
        <FailureState
          message={`Your applications couldn't be read again, so what is shown may be out of date: ${list.refreshFailed}`}
          onRetry={onRetry}
        />
      )}
      <div className="bj-app-toolbar">
        <h2 className="bj-app-heading" id={LIST_HEADING_ID}>
          Tracked jobs <span className="bj-app-count">{count}</span>
        </h2>
        {!props.phone && (
          <ViewToggle
            label="View"
            options={[
              { value: "list", label: "List" },
              { value: "board", label: "Board" },
            ]}
            value={props.layout}
            onChange={props.onLayoutChange}
          />
        )}
      </div>
      <Cards props={props} applications={list.applications} />
    </section>
  );
}

export function ApplicationsView(props: ApplicationsViewProps) {
  return (
    <div className="bj-apps">
      <div className="bj-visually-hidden" role="status" aria-live="polite">
        {props.announcement}
      </div>
      <PageHeader
        title="Applications"
        subtitle="The jobs you track, each with its status. Move one when its real status changes."
        actions={
          // With nothing tracked the band's own button leads to the same form, so there is one way
          // to it, not two.
          props.list.kind === "ready" && props.list.applications.length === 0 ? undefined : (
            <button type="button" className="bj-link-button bj-app-track-link" onClick={props.onTrack}>
              Track a job
            </button>
          )
        }
      />
      <NextStepBand outcome={props.outcome} onAction={props.onBandAction} />
      <ListBody {...props} />
      <section className="bj-app-track" aria-labelledby={TRACK_HEADING_ID}>
        <h2 className="bj-app-heading" id={TRACK_HEADING_ID}>
          Track a job
        </h2>
        <div className="bj-app-track-grid">
          <UrlIngestForm onCreated={props.onCreated} />
          <PasteJobForm onCreated={props.onCreated} />
        </div>
      </section>
    </div>
  );
}
