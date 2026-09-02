import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";
import type { TrackDiscoveredJobBody } from "../lib/discoverTypes";

// Today (Horizon Sprint 4.0) -- Proposal §37.1's daily control surface,
// scoped to the three items the digest listener could genuinely produce
// from real events: a job tracked, a resume generated or failed, a stage
// change. Job Finder P9 adds a fourth: a high-fit new job found by the
// background saved-search matcher (today-feed-job-matching.md) -- the
// other four remaining §37.1 bullets (outreach followups, interview prep,
// stale applications, artifact approval) still depend on capabilities
// that don't exist yet.

type TodayItemKind =
  | "job_tracked"
  | "resume_ready"
  | "resume_failed"
  | "stage_changed"
  | "high_fit_job";

// The today_item_job_matches companion row (today-feed-job-matching.md
// D8) -- present only on kind="high_fit_job" items.
interface JobMatch {
  today_item_id: string;
  saved_search_id: string;
  apply_url: string;
  title: string;
  company: string | null;
  location: string | null;
  score100: number;
  bin: string;
  snippet: string;
  provider: string;
}

interface TodayItem {
  id: string;
  kind: TodayItemKind;
  headline: string;
  detail: string | null;
  created_at: string;
  job_match: JobMatch | null;
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
    case "high_fit_job":
      // D5: only Strong-bin (score100 >= 70) matches ever become Today
      // items, so a single fixed color is correct, not a lookup.
      return "bj-badge-emerald";
  }
}

function trackRequestBody(match: JobMatch): TrackDiscoveredJobBody {
  return {
    apply_url: match.apply_url,
    title: match.title,
    company: match.company,
    location: match.location,
    snippet: match.snippet,
    provider: match.provider,
  };
}

export default function Today() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [dismissing, setDismissing] = useState<string | null>(null);
  const [tracking, setTracking] = useState<string | null>(null);
  const [tracked, setTracked] = useState<Record<string, string>>({});
  const [trackError, setTrackError] = useState<string | null>(null);

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

  // Reuses `POST /discover/track` exactly as built in Job Finder P8 -- same
  // request shape, same endpoint, no new write path for a job surfaced via
  // the Today feed instead of Discover.
  async function track(match: JobMatch) {
    setTracking(match.apply_url);
    setTrackError(null);
    try {
      const application = await apiFetch<{ id: string }>("/discover/track", {
        method: "POST",
        body: JSON.stringify(trackRequestBody(match)),
      });
      setTracked((prev) => ({ ...prev, [match.apply_url]: application.id }));
    } catch (e) {
      setTrackError(e instanceof Error ? e.message : "Failed to track");
    } finally {
      setTracking(null);
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
            didn't), a stage change, or a high-fit new job found for one of your saved searches --
            on either Telegram or web. It doesn't yet cover outreach followups or interview prep,
            since those don't exist yet. Track a job, generate a resume, or save a search on the
            Discover page to see something here.
          </p>
        </div>
      )}

      {trackError && <div className="bj-error">{trackError}</div>}

      {state.kind === "ready" && state.items.length > 0 && (
        <div className="bj-today-list">
          {state.items.map((item) => (
            <div key={item.id} className="bj-card bj-today-item">
              <div className="bj-today-item-main">
                <span className={badgeClass(item.kind)}>{item.kind.replace(/_/g, " ")}</span>
                <div className="bj-today-item-body">
                  <div>{item.headline}</div>
                  {item.detail && <div className="bj-muted bj-small">{item.detail}</div>}
                  {item.job_match && (
                    <div className="bj-muted bj-small">
                      {item.job_match.company ?? "Unknown company"}
                      {item.job_match.location ? ` -- ${item.job_match.location}` : ""}
                      {` -- ${item.job_match.score100} / 100`}
                    </div>
                  )}
                </div>
              </div>
              <div className="bj-actions">
                {item.job_match &&
                  (tracked[item.job_match.apply_url] ? (
                    <Link to={`/applications/${tracked[item.job_match.apply_url]}`}>
                      Open workspace
                    </Link>
                  ) : (
                    <button
                      onClick={() => void track(item.job_match!)}
                      disabled={tracking === item.job_match.apply_url}
                    >
                      {tracking === item.job_match.apply_url ? "Tracking..." : "Track"}
                    </button>
                  ))}
                <button onClick={() => void dismiss(item.id)} disabled={dismissing === item.id}>
                  {dismissing === item.id ? "..." : "Dismiss"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
