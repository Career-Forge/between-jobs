import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";
import type { TrackDiscoveredJobBody } from "../lib/discoverTypes";

// Today (Horizon Sprint 4.0) -- Proposal §37.1's daily control surface,
// scoped to the three items the digest listener could genuinely produce
// from real events: a job tracked, a resume generated or failed, a stage
// change. Job Finder P9 adds a fourth: a high-fit new job found by the
// background saved-search matcher (today-feed-job-matching.md). Gmail
// reply/status parsing R4 adds a fifth: a below-threshold reply
// classification worth a human's review -- the other three remaining
// §37.1 bullets (interview prep, stale applications, artifact approval)
// still depend on capabilities that don't exist yet.

type TodayItemKind =
  | "job_tracked"
  | "resume_ready"
  | "resume_failed"
  | "stage_changed"
  | "high_fit_job"
  | "status_proposal";

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

// The application_status_proposals row (gmail-reply-status-parsing.md
// R3/R4) -- present only on kind="status_proposal" items, embedded via a
// thin today_item_status_proposals link (unlike job_match, there's no
// companion data copy: this IS the same canonical row Accept/Dismiss act
// on directly).
interface StatusProposal {
  id: string;
  proposed_type: string;
  confidence: number;
  evidence_spans: string[];
  status: "pending" | "accepted" | "dismissed";
}

interface TodayItem {
  id: string;
  kind: TodayItemKind;
  headline: string;
  detail: string | null;
  created_at: string;
  job_match: JobMatch | null;
  status_proposal: StatusProposal | null;
}

// Mirrors the backend's own `application_status_proposals_store.STAGE_
// MAP` -- only these proposed types correspond to a real Kanban stage, so
// only these ever show an "Accept" button. `application.acknowledged`/
// `recruiter.replied`/`unknown` still show, just Dismiss-only.
const MAPPABLE_PROPOSED_TYPES = new Set([
  "assessment.received",
  "interview.requested",
  "interview.scheduled",
  "application.rejected",
  "offer.received",
]);

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
    case "status_proposal":
      return "bj-badge-violet";
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
  // A Set, not a single id -- an adversarial review found a plain
  // `string | null` here meant resolving one status_proposal item while
  // a DIFFERENT one's own request was still in flight would clobber each
  // other's disabled/"..." state, since both write to the same variable.
  const [resolvingProposals, setResolvingProposals] = useState<Set<string>>(new Set());

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

  function removeItem(itemId: string) {
    setState((prev) =>
      prev.kind === "ready" ? { ...prev, items: prev.items.filter((i) => i.id !== itemId) } : prev,
    );
  }

  async function dismiss(itemId: string) {
    setDismissing(itemId);
    try {
      await apiFetch(`/today/${itemId}/dismiss`, { method: "POST" });
      removeItem(itemId);
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to dismiss" });
    } finally {
      setDismissing(null);
    }
  }

  // Both retire the Today item server-side too (POST /today/{id}/
  // accept-proposal and .../dismiss-proposal), so removing it here on
  // success mirrors dismiss()'s own behavior exactly -- the difference
  // is which underlying decision was recorded.
  function markResolving(itemId: string) {
    setResolvingProposals((prev) => new Set(prev).add(itemId));
  }

  function clearResolving(itemId: string) {
    setResolvingProposals((prev) => {
      const next = new Set(prev);
      next.delete(itemId);
      return next;
    });
  }

  async function acceptProposal(itemId: string) {
    markResolving(itemId);
    try {
      await apiFetch(`/today/${itemId}/accept-proposal`, { method: "POST" });
      removeItem(itemId);
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to accept" });
    } finally {
      clearResolving(itemId);
    }
  }

  async function dismissProposal(itemId: string) {
    markResolving(itemId);
    try {
      await apiFetch(`/today/${itemId}/dismiss-proposal`, { method: "POST" });
      removeItem(itemId);
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to dismiss" });
    } finally {
      clearResolving(itemId);
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
            didn't), a stage change, a high-fit new job found for one of your saved searches, or a
            Gmail reply worth a second look -- on either Telegram or web. It doesn't yet cover
            interview prep or stale-application nudges, since those don't exist yet. Track a job,
            generate a resume, or save a search on the Discover page to see something here.
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
                  {item.status_proposal &&
                    item.status_proposal.evidence_spans.map((span, i) => (
                      <div key={i} className="bj-muted bj-small">
                        &ldquo;{span}&rdquo;
                      </div>
                    ))}
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
                {item.status_proposal ? (
                  <>
                    {MAPPABLE_PROPOSED_TYPES.has(item.status_proposal.proposed_type) && (
                      <button
                        onClick={() => void acceptProposal(item.id)}
                        disabled={resolvingProposals.has(item.id)}
                      >
                        {resolvingProposals.has(item.id) ? "..." : "Accept"}
                      </button>
                    )}
                    <button
                      onClick={() => void dismissProposal(item.id)}
                      disabled={resolvingProposals.has(item.id)}
                    >
                      {resolvingProposals.has(item.id) ? "..." : "Dismiss"}
                    </button>
                  </>
                ) : (
                  <button onClick={() => void dismiss(item.id)} disabled={dismissing === item.id}>
                    {dismissing === item.id ? "..." : "Dismiss"}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
