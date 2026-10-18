import type { Failure } from "../../lib/setupRequired";
import { rowActions, type RowView, type TodayItem } from "../../lib/todayFeed";
import { ItemActions, ItemBody, ItemProblem, type BusyKind, type ItemActionsProps } from "./ItemParts";
import "../../styles/today.css";

// The item the next-step band holds. The band already carries the sentence and the item's main
// action (and "Generate anyway" for a held-back resume), so what is drawn here is the item's own
// words -- the chip, the detail, the reply's wording, its trail line -- and a quiet Dismiss. The
// item is taken out of the list below, so no action shows twice.

export interface LitItemProps {
  item: TodayItem;
  view: RowView;
  busy: BusyKind | undefined;
  problem: Failure | undefined;
  onAction: ItemActionsProps["onAction"];
  onRetryProblem: () => void;
}

export function LitItem({ item, view, busy, problem, onAction, onRetryProblem }: LitItemProps) {
  const dismiss = rowActions(item, null).filter((action) => action.id === "dismiss");
  return (
    <>
      <ItemBody view={view} />
      {problem !== undefined && <ItemProblem problem={problem} onRetry={onRetryProblem} />}
      <ItemActions item={item} actions={dismiss} busy={busy} onAction={onAction} />
    </>
  );
}
