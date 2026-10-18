import { Link } from "react-router-dom";
import type { Failure } from "../../lib/setupRequired";
import { SETTINGS_PATH } from "../../lib/settingsPaths";
import type { RowAction, RowView, TodayItem } from "../../lib/todayFeed";
import { FailureState, SetupRequiredState } from "../paper/StateViews";
import { TrailLine } from "../paper/TrailLine";
import "../../styles/paper.css";
import "../../styles/today.css";

// The parts of one Today item, drawn the same way in a list row and in the band that holds the lit
// one. What each says is lib/todayFeed.ts (rowView); this only draws it.

export type BusyKind = "accept" | "track" | "generate-anyway" | "dismiss";

// The words on a control while its own request is out.
export const BUSY_LABELS: Record<BusyKind, string> = {
  accept: "Moving...",
  track: "Tracking...",
  "generate-anyway": "Generating...",
  dismiss: "Dismissing...",
};

// The kind in muted mono capitals. A held-back resume's red cross is the row's marker, not part of this line.
export function KindLine({ view }: { view: RowView }) {
  return <p className="bj-today-kind">{view.label}</p>;
}

// The words under the headline: the chip, the detail lines, the reply's own words, then the trail.
// The band shows the sentence in place of the headline, so it asks for the body only.
export function ItemBody({ view }: { view: RowView }) {
  return (
    <>
      {view.chip !== null && (
        <p className="bj-today-chip">
          <span aria-hidden="true">{"◆"}</span>
          {view.chip.text}
        </p>
      )}
      {view.details.map((line, index) => (
        <p key={`${index}-${line}`} className="bj-today-detail">
          {line}
        </p>
      ))}
      {view.quotes.map((quote, index) => (
        <p key={`${index}-${quote}`} className="bj-today-quote">
          {"“"}
          {quote}
          {"”"}
        </p>
      ))}
      <TrailLine trail={view.trail} receipt={view.receipt} receiptTitle="Where this came from" />
    </>
  );
}

// An item's own failed action, under the item. A setup failure names what is missing and links to
// where it is fixed (always: when the server sent no usable link, the settings page); anything else
// is a callout with Retry, which sends the same request again. Both are announced when they appear:
// the person pressed a button and is waiting to hear how it went (the failure callout is an alert of
// its own, so the setup notice is one too).
export function ItemProblem({ problem, onRetry }: { problem: Failure; onRetry: () => void }) {
  if (problem.kind === "setup") {
    const { notice } = problem;
    return (
      <div className="bj-today-problem" role="alert">
        <SetupRequiredState
          message={notice.message}
          missing={[{ label: notice.linkLabel ?? "Open Settings", to: notice.linkTo ?? SETTINGS_PATH }]}
        />
      </div>
    );
  }
  return (
    <div className="bj-today-problem">
      <FailureState message={problem.message} onRetry={onRetry} />
    </div>
  );
}

export interface ItemActionsProps {
  item: TodayItem;
  actions: readonly RowAction[];
  busy: BusyKind | undefined;
  onAction: (action: Exclude<RowAction["id"], "link">, item: TodayItem) => void;
  className?: string;
}

// The controls of an item. Row actions are secondary (outlined); Dismiss is quiet. A control that
// is waiting on its request is aria-disabled, not disabled, so focus stays where it was.
export function ItemActions({ item, actions, busy, onAction, className = "bj-today-item-actions" }: ItemActionsProps) {
  if (actions.length === 0) return null;
  return (
    <div className={className}>
      {actions.map((action) => {
        if (action.id === "link") {
          return (
            <Link key={`${action.label}-${action.to}`} className="bj-button-link bj-today-action" to={action.to}>
              {action.label}
            </Link>
          );
        }
        const id = action.id;
        return (
          <button
            key={id}
            type="button"
            className={id === "dismiss" ? "bj-quiet bj-today-action" : "bj-today-action"}
            aria-disabled={busy !== undefined}
            onClick={() => {
              if (busy === undefined) onAction(id, item);
            }}
          >
            {busy === id ? BUSY_LABELS[id] : action.label}
          </button>
        );
      })}
    </div>
  );
}
