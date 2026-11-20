import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { NextStepOutcome } from "../../lib/nextStep";
import type { TodayRowId } from "../../lib/nextStep/pages/today";
import type { Failure } from "../../lib/setupRequired";
import { kindLabel, rowView, type BackgroundView, type RowContext, type TodayItem } from "../../lib/todayFeed";
import type { PipelineView } from "../../lib/todayPipeline";
import { todayIntent } from "../../lib/nextStep/pages/today";
import { NextStepBand, type NextStepPending } from "../paper/NextStepBand";
import { PageHeader } from "../paper/PageHeader";
import { FailureState, LoadingState, PartialState } from "../paper/StateViews";
import { TodayEmptyState } from "../TodayEmptyState";
import { BackgroundTrail } from "./BackgroundTrail";
import { BUSY_LABELS, type BusyKind, type ItemActionsProps } from "./ItemParts";
import { LitItem } from "./LitItem";
import { PipelineCard } from "./PipelineCard";
import { TodayRow } from "./TodayRow";
import "../../styles/today.css";

// Today as a function of what it has read: the header, the next-step band (which holds the lit item
// itself), then the page in one of two layouts.
//
//   from 1280px   two columns. The feed (or what to expect from it) is on the left. The right column
//                 holds, top to bottom, the setup checklist in whichever of its forms it is in (the
//                 open list, its one folded line, or nothing), the Pipeline card and the
//                 "In the background" trail.
//   under that    one column: "In the background" as one line under the band, then the checklist, the
//                 feed, and the Pipeline card as the last section.
//
// Every state of the page is drawn here from props, so each can be shown and tested on its own; the
// reading, the requests and the handlers are pages/Today.tsx.

export type FeedState =
  | { state: "loading" }
  | { state: "failed"; message: string }
  | { state: "ready"; items: readonly TodayItem[] };

// What the setup and applications reads said, as far as the body is concerned. A missing profile or
// key is not drawn here: the band carries the fix for a missing profile, and the checklist names
// each missing step with its link.
export interface AccountNotes {
  /** The profile or credentials read failed. The band holds Try again, so the body has none. */
  setupFailed: boolean;
  /** The applications list could not be read, so items carry no job names. */
  applicationsFailed: boolean;
}

export interface TodayViewProps {
  now: Date;
  /** From 1280px: the checklist, the Pipeline card and the trail are a right column. Below: one column. */
  wide: boolean;
  outcome: NextStepOutcome;
  /** The row of the rule table that decided, when it is one that lights an item. */
  litRow: TodayRowId | null;
  /** The item the band holds. It is left out of the list. */
  litItem: TodayItem | null;
  feed: FeedState;
  context: RowContext;
  /** The application a Track in this visit created, by the job's apply URL. */
  tracked: Readonly<Record<string, string>>;
  busy: Readonly<Record<string, BusyKind | undefined>>;
  problems: Readonly<Record<string, Failure | undefined>>;
  background: BackgroundView;
  /** How many applications sit at each stage, from the applications read (lib/todayPipeline.ts). */
  pipeline: PipelineView;
  account: AccountNotes;
  /** The setup checklist (it reads its own flags from the browser). */
  checklist: ReactNode;
  /** What was just done, for a screen reader: the polite status the page speaks through. */
  announcement: string;
  /** A confirmation that outlives the item it was about (a forced generation retires its card). */
  confirmation: TodayConfirmation | null;
  onBandAction: (intent: string) => void;
  onItemAction: ItemActionsProps["onAction"];
  onRetryProblem: (itemId: string) => void;
  onRetryAccount: () => void;
}

export interface TodayConfirmation {
  text: string;
  /** Where to see the result. */
  link: { label: string; to: string };
}

// The request the lit item has out, in the band's terms: the button it was for reads its busy
// words, and every handler button waits. A Dismiss has no button in the band, so it renames none.
function pendingFor(item: TodayItem | null, busy: TodayViewProps["busy"]): NextStepPending | null {
  const kind = item === null ? undefined : busy[item.id];
  if (item === null || kind === undefined) return null;
  return kind === "dismiss" ? { intent: null } : { intent: todayIntent(kind, item.id), label: BUSY_LABELS[kind] };
}

// The id of the list's heading: where focus goes when a row it held is gone.
export const ACTIVITY_HEADING_ID = "bj-today-activity";

function eyebrowFor(row: TodayRowId | null, item: TodayItem | null): ReactNode {
  if (row === "T2") {
    return (
      <>
        <span className="bj-band-eyebrow-glyph" aria-hidden="true">
          {"✕"}
        </span>
        Resume not generated
      </>
    );
  }
  return item === null ? undefined : `Next step · ${kindLabel(item.kind)}`;
}

function trackedFor(item: TodayItem, tracked: Readonly<Record<string, string>>): string | null {
  return item.job_match === null ? null : (tracked[item.job_match.apply_url] ?? null);
}

export function TodayView(props: TodayViewProps) {
  const { now, wide, outcome, litRow, litItem, feed, context, tracked, busy, problems, background, account } = props;
  // The lit item's cost note (a held-back resume's Generate anyway) is the description of the band's second button,
  // which the item sits under. It is named only while the note is on the page.
  const litNoteId = useId();
  const litView = litItem === null ? null : rowView(litItem, context);
  const litNoteOnPage = litView !== null && litView.spendNote !== undefined;
  const items = feed.state === "ready" ? feed.items.filter((item) => item.id !== litItem?.id) : [];
  const hasNamedItems = feed.state === "ready" && feed.items.some((item) => item.application_id != null);
  // Until the band knows what it holds, the list is held back too: the item it will lift out would
  // otherwise show here with live buttons, and then move.
  const holding = feed.state === "ready" && feed.items.length > 0 && outcome.status === "loading";

  const body = (
    <div className="bj-today-main">
      {account.setupFailed && (
        <div className="bj-today-stack">
          <FailureState message="Your profile or your credentials couldn't be read, so setup can't be checked. Nothing was lost." />
        </div>
      )}
      {props.confirmation !== null && (
        <p className="bj-today-confirmation">
          {props.confirmation.text} <Link to={props.confirmation.link.to}>{props.confirmation.link.label}</Link>
        </p>
      )}
      {!wide && props.checklist}
      {(feed.state === "loading" || holding) && (
        <div className="bj-today-section">
          <LoadingState rows={3} rowHeight={72} label="Loading Today..." />
        </div>
      )}
      {feed.state === "failed" && (
        <div className="bj-today-stack">
          <FailureState message={`Today's items couldn't be read: ${feed.message}`} />
        </div>
      )}
      {feed.state === "ready" && account.applicationsFailed && hasNamedItems && (
        <div className="bj-today-stack">
          <PartialState
            lead="Some of this page couldn't be read:"
            notices={["Your applications couldn't be read, so these items appear without their job names."]}
          />
        </div>
      )}
      {feed.state === "ready" && feed.items.length === 0 && (
        <div className="bj-today-section">
          <TodayEmptyState />
        </div>
      )}
      {items.length > 0 && !holding && (
        <section className="bj-today-section" aria-labelledby={ACTIVITY_HEADING_ID}>
          <h2 className="bj-today-heading" id={ACTIVITY_HEADING_ID} tabIndex={-1}>
            Recent activity
          </h2>
          <ul className="bj-today-list">
            {items.map((item) => {
              const trackedId = trackedFor(item, tracked);
              return (
                <TodayRow
                  key={item.id}
                  item={item}
                  view={rowView(item, context)}
                  trackedApplicationId={trackedId}
                  fit={item.application_id == null ? undefined : context.fits[item.application_id]}
                  busy={busy[item.id]}
                  problem={problems[item.id]}
                  onAction={props.onItemAction}
                  onRetryProblem={() => props.onRetryProblem(item.id)}
                />
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );

  return (
    <div className="bj-today">
      <div className="bj-visually-hidden" role="status" aria-live="polite">
        {props.announcement}
      </div>
      <PageHeader title="Today" subtitle="What happened on your jobs and saved searches, and the one thing to do next." />
      <NextStepBand outcome={outcome} onAction={props.onBandAction} eyebrow={eyebrowFor(litRow, litItem)} pending={pendingFor(litItem, busy)} describedBy={{ secondary: litNoteOnPage ? litNoteId : undefined }}>
        {litItem !== null && litView !== null && (
          <LitItem
            item={litItem}
            view={litView}
            spendNoteId={litNoteOnPage ? litNoteId : undefined}
            busy={busy[litItem.id]}
            problem={problems[litItem.id]}
            onAction={props.onItemAction}
            onRetryProblem={() => props.onRetryProblem(litItem.id)}
          />
        )}
      </NextStepBand>
      {wide ? (
        <div className="bj-today-grid">
          {body}
          <div className="bj-today-side">
            {props.checklist}
            <PipelineCard view={props.pipeline} placement="side" onRetry={props.onRetryAccount} />
            <BackgroundTrail view={background} now={now} placement="margin" onRetry={props.onRetryAccount} timeZone={context.timeZone} />
          </div>
        </div>
      ) : (
        <>
          <BackgroundTrail view={background} now={now} placement="inline" onRetry={props.onRetryAccount} timeZone={context.timeZone} />
          {body}
          <PipelineCard view={props.pipeline} placement="inline" onRetry={props.onRetryAccount} />
        </>
      )}
    </div>
  );
}
