import { rowActions, type FitRead, type RowView, type TodayItem } from "../../lib/todayFeed";
import type { Failure } from "../../lib/setupRequired";
import { ItemActions, ItemBody, ItemProblem, KindLine, type BusyKind, type ItemActionsProps } from "./ItemParts";
import "../../styles/today.css";

// One feed item as a list row: a marker, the kind, the headline, what it says about itself with its
// trail line last, and its controls on the right (under the text on a phone).

function marker(view: RowView): { glyph: string; tone: "gold" | "muted" | "danger" } {
  // A held-back card is marked red only while it is waiting on the person (it carries its chip).
  if (view.kind === "resume_failed" && view.chip !== null) return { glyph: "✕", tone: "danger" };
  // Waiting on you: a gold diamond. Logged by plain code with nothing to decide: the muted circle.
  return view.chip !== null ? { glyph: "◆", tone: "gold" } : { glyph: "○", tone: "muted" };
}

export interface TodayRowProps {
  item: TodayItem;
  view: RowView;
  trackedApplicationId: string | null;
  /** What the stored fit check said, for a held-back card. */
  fit?: FitRead;
  busy: BusyKind | undefined;
  problem: Failure | undefined;
  onAction: ItemActionsProps["onAction"];
  onRetryProblem: () => void;
}

export function TodayRow({ item, view, trackedApplicationId, fit, busy, problem, onAction, onRetryProblem }: TodayRowProps) {
  const mark = marker(view);
  return (
    <li className="bj-today-row" data-kind={item.kind}>
      <span className={`bj-today-marker bj-today-marker--${mark.tone}`} aria-hidden="true">
        {mark.glyph}
      </span>
      <div className="bj-today-row-body">
        <KindLine view={view} />
        <p className={view.serifTitle ? "bj-today-title bj-today-title--serif" : "bj-today-title"}>{view.title}</p>
        <ItemBody view={view} />
        {problem !== undefined && <ItemProblem problem={problem} onRetry={onRetryProblem} />}
      </div>
      <ItemActions
        item={item}
        actions={rowActions(item, trackedApplicationId, fit)}
        busy={busy}
        onAction={onAction}
        className="bj-today-item-actions bj-today-row-actions"
      />
    </li>
  );
}
