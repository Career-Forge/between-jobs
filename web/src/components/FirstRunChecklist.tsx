import { useAuth } from "../auth";
import { useFirstRun } from "../lib/useFirstRun";
import { FirstRunChecklistView } from "./FirstRunChecklistView";

// The first-run checklist at the top of Today. Per user: keyed by the user id, so the
// dismissed flag and the answers are never one account's carried over to another.
export function FirstRunChecklist() {
  const { session } = useAuth();
  if (!session) return null;
  return <FirstRunChecklistFor key={session.user.id} userId={session.user.id} />;
}

function FirstRunChecklistFor({ userId }: { userId: string }) {
  const { view, dismiss } = useFirstRun(userId);
  return <FirstRunChecklistView view={view} onDismiss={dismiss} />;
}
