import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";

// Today (Horizon Sprint 4.0) -- Proposal §37.1's daily control surface,
// scoped to the three items the digest listener can genuinely produce
// from real events today: a job tracked, a resume generated or failed,
// a stage change. The other five §37.1 bullets (high-fit jobs, outreach
// followups, interview prep, stale applications, artifact approval) all
// depend on capabilities that don't exist yet (Discovery, Outreach,
// Practice) or a mechanic nothing produces yet -- the empty state below
// says so honestly rather than implying they're just not loaded.

type TodayItemKind = "job_tracked" | "resume_ready" | "resume_failed" | "stage_changed";

interface TodayItem {
  id: string;
  kind: TodayItemKind;
  headline: string;
  detail: string | null;
  created_at: string;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; items: TodayItem[] };

function badgeClass(kind: TodayItemKind): string {
  switch (kind) {
    case "job_tracked":
      return "bj-badge-cyan";
    case "resume_ready":
      return "bj-badge-emerald";
    case "resume_failed":
      return "bj-badge-danger";
    case "stage_changed":
      return "bj-badge-gold";
  }
}

export default function Today() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [dismissing, setDismissing] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const items = await apiFetch<TodayItem[]>("/today");
      setState({ kind: "ready", items });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function dismiss(itemId: string) {
    setDismissing(itemId);
    try {
      await apiFetch(`/today/${itemId}/dismiss`, { method: "POST" });
      setState((prev) =>
        prev.kind === "ready"
          ? { ...prev, items: prev.items.filter((i) => i.id !== itemId) }
          : prev,
      );
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to dismiss" });
    } finally {
      setDismissing(null);
    }
  }

  return (
    <div>
      <h1>Today</h1>

      {state.kind === "loading" && <div className="bj-muted bj-small">Loading...</div>}

      {state.kind === "error" && (
        <div>
          <div className="bj-error">{state.message}</div>
          <button onClick={() => void load()}>Retry</button>
        </div>
      )}

      {state.kind === "ready" && state.items.length === 0 && (
        <div className="bj-empty">
          <h2>Nothing here yet</h2>
          <p>
            Today shows what actually happened: a job you tracked, a resume that generated (or
            didn't), a stage change -- on either Telegram or web. It doesn't yet cover high-fit
            new jobs, outreach followups, or interview prep, since Discovery, Outreach, and
            Practice don't exist yet. Track a job or generate a resume to see something here.
          </p>
        </div>
      )}

      {state.kind === "ready" && state.items.length > 0 && (
        <div className="bj-today-list">
          {state.items.map((item) => (
            <div key={item.id} className="bj-card bj-today-item">
              <div className="bj-today-item-main">
                <span className={badgeClass(item.kind)}>{item.kind.replace("_", " ")}</span>
                <div>
                  <div>{item.headline}</div>
                  {item.detail && <div className="bj-muted bj-small">{item.detail}</div>}
                </div>
              </div>
              <button onClick={() => void dismiss(item.id)} disabled={dismissing === item.id}>
                {dismissing === item.id ? "..." : "Dismiss"}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
