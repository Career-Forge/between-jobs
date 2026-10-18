import type { ReactNode } from "react";
import type { NextStepOutcome } from "../../lib/nextStep";
import type { TodayRowId } from "../../lib/nextStep/pages/today";
import type { Failure } from "../../lib/setupRequired";
import { kindLabel, rowView, type BackgroundView, type RowContext, type TodayItem } from "../../lib/todayFeed";
import { NextStepBand } from "../paper/NextStepBand";
import { PageHeader } from "../paper/PageHeader";
import { FailureState, LoadingState, PartialState } from "../paper/StateViews";
import { TodayEmptyState } from "../TodayEmptyState";
import { BackgroundTrail } from "./BackgroundTrail";
import type { BusyKind, ItemActionsProps } from "./ItemParts";
import { LitItem } from "./LitItem";
import { TodayRow } from "./TodayRow";
import "../../styles/today.css";

// Today as a function of what it has read: the header, the next-step band (which holds the lit item
// itself), then the body -- setup, the checklist, the feed or what to expect from it -- and the
// "In the background" trail, in a right margin from 1280px and as one line under the band below that.
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
  /** From 1280px: the trail is a right margin. Below: one line under the band. */
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
  account: AccountNotes;
  /** The setup checklist (it reads its own flags from the browser). */
  checklist: ReactNode;
  onBandAction: (intent: string) => void;
  onItemAction: ItemActionsProps["onAction"];
  onRetryProblem: (itemId: string) => void;
  onRetryAccount: () => void;
}

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
  const items = feed.state === "ready" ? feed.items.filter((item) => item.id !== litItem?.id) : [];
  const hasNamedItems = feed.state === "ready" && feed.items.some((item) => item.application_id != null);

  const body = (
    <div className="bj-today-main">
      {account.setupFailed && (
        <div className="bj-today-stack">
          <FailureState message="Your profile or your credentials couldn't be read, so setup can't be checked. Nothing was lost." />
        </div>
      )}
      {props.checklist}
      {feed.state === "loading" && (
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
      {items.length > 0 && (
        <section className="bj-today-section" aria-labelledby="bj-today-activity">
          <h2 className="bj-today-heading" id="bj-today-activity">
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
      <PageHeader title="Today" subtitle="What happened on your jobs and saved searches, and the one thing to do next." />
      <NextStepBand outcome={outcome} onAction={props.onBandAction} eyebrow={eyebrowFor(litRow, litItem)}>
        {litItem !== null && (
          <LitItem
            item={litItem}
            view={rowView(litItem, context)}
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
          <BackgroundTrail view={background} now={now} placement="margin" onRetry={props.onRetryAccount} timeZone={context.timeZone} />
        </div>
      ) : (
        <>
          <BackgroundTrail view={background} now={now} placement="inline" onRetry={props.onRetryAccount} timeZone={context.timeZone} />
          {body}
        </>
      )}
    </div>
  );
}
