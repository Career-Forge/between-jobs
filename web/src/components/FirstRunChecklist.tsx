import { useAuth } from "../auth";
import type { FirstRunFacts } from "../lib/firstRun";
import { useFirstRun } from "../lib/useFirstRun";
import { FirstRunChecklistView } from "./FirstRunChecklistView";

// The setup checklist on Today. Per user: keyed by the user id, so the hidden flag and the answers are
// never one account's carried over to another. It reads nothing itself: Today has read the account
// and hands the answers in.
export function FirstRunChecklist({ facts, onRetry }: { facts: FirstRunFacts; onRetry?: () => void }) {
  const { session } = useAuth();
  if (!session) return null;
  return <FirstRunChecklistFor key={session.user.id} userId={session.user.id} facts={facts} onRetry={onRetry} />;
}

function FirstRunChecklistFor({ userId, facts, onRetry }: { userId: string; facts: FirstRunFacts; onRetry?: () => void }) {
  const { view, dismiss } = useFirstRun(userId, facts);
  return <FirstRunChecklistView view={view} onDismiss={dismiss} onRetry={onRetry} />;
}
