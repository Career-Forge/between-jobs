import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ScoreBar } from "../components/ScoreBreakdown";
import { apiFetch, ApiError } from "../lib/api";
import {
  binBadgeClass,
  formatPostedDate,
  formatSalary,
  locationLabel,
  savedSearchLabel,
  subScoreLabel,
  trackRequestBody,
} from "../lib/discover";
import type { DiscoverResponse, JobCard, SavedSearch } from "../lib/discoverTypes";

// Discover (Job Finder P8, job-finder-port.md's own build order) -- the
// full native search pipeline: P4's 9 BYOK live-search providers + P1-P3e's
// own registry lane, merged via P5's dedup/filter, P7's per-platform
// liveness check, and P6's batch /100 fit score, all in one request.
// Replaces Horizon Sprint 4.1's read-only n8n-registry facade. "Track"
// reuses the same manual-paste job ingestion Applications/Telegram already
// use -- one write path, several ways to reach it.

type State =
  | { kind: "loading" }
  | { kind: "not_configured"; message: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; result: DiscoverResponse };

export default function Discover() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const [companies, setCompanies] = useState("");
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [tracking, setTracking] = useState<string | null>(null);
  const [tracked, setTracked] = useState<Record<string, string>>({});
  const [trackError, setTrackError] = useState<string | null>(null);
  const [savedSearches, setSavedSearches] = useState<SavedSearch[]>([]);
  const [savingSearch, setSavingSearch] = useState(false);
  const [savedSearchError, setSavedSearchError] = useState<string | null>(null);

  const search = useCallback(
    async (params: { q: string; location: string; companies: string; remoteOnly: boolean }) => {
      setState({ kind: "loading" });
      const qs = new URLSearchParams();
      if (params.q) qs.set("q", params.q);
      if (params.location) qs.set("location", params.location);
      if (params.companies) qs.set("companies", params.companies);
      if (params.remoteOnly) qs.set("remote_only", "true");
      try {
        const result = await apiFetch<DiscoverResponse>(`/discover?${qs.toString()}`);
        setState({ kind: "ready", result });
      } catch (e) {
        if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
          setState({ kind: "not_configured", message: e.message });
          return;
        }
        setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to search" });
      }
    },
    [],
  );

  const loadSavedSearches = useCallback(async () => {
    try {
      const searches = await apiFetch<SavedSearch[]>("/saved-searches");
      setSavedSearches(searches);
    } catch {
      // Non-fatal -- the search page itself still works without saved
      // searches; the section below just stays empty.
    }
  }, []);

  useEffect(() => {
    void search({ q: "", location: "", companies: "", remoteOnly: false });
    void loadSavedSearches();
  }, [search, loadSavedSearches]);

  function runSearch() {
    void search({ q: query, location, companies, remoteOnly });
  }

  async function saveCurrentSearch() {
    setSavingSearch(true);
    setSavedSearchError(null);
    try {
      const created = await apiFetch<SavedSearch>("/saved-searches", {
        method: "POST",
        body: JSON.stringify({
          query,
          location: location || null,
          companies: companies
            ? companies
                .split(",")
                .map((c) => c.trim())
                .filter(Boolean)
            : [],
          remote_only: remoteOnly,
        }),
      });
      setSavedSearches((prev) => [created, ...prev]);
    } catch (e) {
      setSavedSearchError(e instanceof Error ? e.message : "Failed to save search");
    } finally {
      setSavingSearch(false);
    }
  }

  async function toggleSavedSearch(id: string, isActive: boolean) {
    try {
      const updated = await apiFetch<SavedSearch>(`/saved-searches/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: isActive }),
      });
      setSavedSearches((prev) => prev.map((s) => (s.id === id ? updated : s)));
    } catch (e) {
      setSavedSearchError(e instanceof Error ? e.message : "Failed to update search");
    }
  }

  async function deleteSavedSearch(id: string) {
    try {
      await apiFetch(`/saved-searches/${id}`, { method: "DELETE" });
      setSavedSearches((prev) => prev.filter((s) => s.id !== id));
    } catch (e) {
      setSavedSearchError(e instanceof Error ? e.message : "Failed to delete search");
    }
  }

  async function track(job: JobCard) {
    setTracking(job.apply_url);
    setTrackError(null);
    try {
      const application = await apiFetch<{ id: string }>("/discover/track", {
        method: "POST",
        body: JSON.stringify(trackRequestBody(job)),
      });
      setTracked((prev) => ({ ...prev, [job.apply_url]: application.id }));
    } catch (e) {
      setTrackError(e instanceof Error ? e.message : "Failed to track");
    } finally {
      setTracking(null);
    }
  }

  return (
    <div>
      <h1>Discover</h1>

      <div className="bj-card">
        <label className="bj-field">
          <span>Search</span>
          <input
            type="text"
            value={query}
            placeholder="e.g. staff ai engineer, RAG, python"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
        </label>
        <label className="bj-field">
          <span>Location</span>
          <input
            type="text"
            value={location}
            placeholder="e.g. New York, NY"
            onChange={(e) => setLocation(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
        </label>
        <label className="bj-field">
          <span>Companies</span>
          <input
            type="text"
            value={companies}
            placeholder="e.g. Anthropic, Stripe"
            onChange={(e) => setCompanies(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
        </label>
        <label className="bj-section-editor-toggle bj-small">
          <input
            type="checkbox"
            checked={remoteOnly}
            onChange={(e) => setRemoteOnly(e.target.checked)}
          />
          <span>Remote only</span>
        </label>
        <div className="bj-actions">
          <button className="bj-primary" onClick={runSearch}>
            Search
          </button>
          <button onClick={() => void saveCurrentSearch()} disabled={savingSearch}>
            {savingSearch ? "Saving..." : "Save this search"}
          </button>
        </div>
      </div>

      {savedSearchError && <div className="bj-error">{savedSearchError}</div>}

      {savedSearches.length > 0 && (
        <div className="bj-card">
          <h2>Saved searches</h2>
          <p className="bj-muted bj-small">
            Checked every few hours in the background -- a Strong match shows up in your Today
            feed automatically.
          </p>
          <div className="bj-discover-saved-search-list">
            {savedSearches.map((s) => (
              <div key={s.id} className="bj-discover-saved-search-row">
                <span className={s.is_active ? undefined : "bj-muted"}>
                  {savedSearchLabel(s)}
                </span>
                <div className="bj-actions">
                  <button onClick={() => void toggleSavedSearch(s.id, !s.is_active)}>
                    {s.is_active ? "Pause" : "Resume"}
                  </button>
                  <button onClick={() => void deleteSavedSearch(s.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {state.kind === "loading" && <div className="bj-muted bj-small">Searching...</div>}

      {state.kind === "not_configured" && (
        <div className="bj-empty">
          <h2>Set up your profile and a job-scoring model first</h2>
          <p>{state.message}</p>
        </div>
      )}

      {state.kind === "error" && (
        <div>
          <div className="bj-error">{state.message}</div>
          <button onClick={runSearch}>Retry</button>
        </div>
      )}

      {state.kind === "ready" && (
        <DiscoverResults
          result={state.result}
          tracking={tracking}
          tracked={tracked}
          trackError={trackError}
          onTrack={(job) => void track(job)}
        />
      )}
    </div>
  );
}

function DiscoverResults({
  result,
  tracking,
  tracked,
  trackError,
  onTrack,
}: {
  result: DiscoverResponse;
  tracking: string | null;
  tracked: Record<string, string>;
  trackError: string | null;
  onTrack: (job: JobCard) => void;
}) {
  const totalShown = result.scored.length + result.more.length;

  if (totalShown === 0) {
    return (
      <div className="bj-empty">
        <h2>No results</h2>
        <p>Try a different search, or widen your filters.</p>
        {result.warnings.length > 0 && (
          <p className="bj-muted bj-small">{result.warnings.join(" ")}</p>
        )}
      </div>
    );
  }

  return (
    <div className="bj-discover-list">
      {trackError && <div className="bj-error">{trackError}</div>}
      {result.dead_removed > 0 && (
        <div className="bj-muted bj-small">
          {result.dead_removed} listing{result.dead_removed === 1 ? "" : "s"} dropped -- no
          longer live.
        </div>
      )}
      {result.warnings.length > 0 && (
        <div className="bj-muted bj-small">{result.warnings.join(" ")}</div>
      )}

      {result.scored.map((job) => (
        <JobCardView
          key={job.apply_url}
          job={job}
          tracking={tracking === job.apply_url}
          trackedApplicationId={tracked[job.apply_url] ?? null}
          onTrack={onTrack}
        />
      ))}

      {result.more.length > 0 && (
        <>
          <h2 className="bj-discover-more-heading">More results</h2>
          <p className="bj-muted bj-small">
            Beyond this search's scoring batch -- sorted by tier and recency, not fit.
          </p>
          {result.more.map((job) => (
            <JobCardView
              key={job.apply_url}
              job={job}
              tracking={tracking === job.apply_url}
              trackedApplicationId={tracked[job.apply_url] ?? null}
              onTrack={onTrack}
            />
          ))}
        </>
      )}
    </div>
  );
}

function JobCardView({
  job,
  tracking,
  trackedApplicationId,
  onTrack,
}: {
  job: JobCard;
  tracking: boolean;
  trackedApplicationId: string | null;
  onTrack: (job: JobCard) => void;
}) {
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency);
  const posted = formatPostedDate(job.posted_at);
  const applicableSubScores = job.score
    ? Object.entries(job.score.sub_scores).filter(
        ([key]) => !job.score!.inapplicable_dims.includes(key),
      )
    : [];

  return (
    <div className="bj-card bj-discover-card">
      <div className="bj-card-header">
        <h3>{job.title}</h3>
        {job.score && (
          <span className={binBadgeClass(job.score.bin)}>
            {job.score.bin} -- {job.score.score100} / 100
          </span>
        )}
      </div>
      <div className="bj-muted bj-small">
        {job.company ?? "Unknown company"} -- {locationLabel(job)}
      </div>
      {(salary || posted) && (
        <div className="bj-muted bj-small">
          {[salary, posted].filter(Boolean).join(" -- ")}
        </div>
      )}
      {job.snippet && <p className="bj-small">{job.snippet}</p>}

      {job.score && (
        <div className="bj-honest-floor-section">
          <span className="bj-honest-floor-section-label">{job.score.one_liner}</span>
          <div className="bj-score-rows">
            {applicableSubScores.map(([key, value]) => (
              <ScoreBar key={key} label={subScoreLabel(key)} value={value} max={100} />
            ))}
          </div>
        </div>
      )}

      <div className="bj-actions">
        {job.link_checked ? (
          <span className="bj-badge-muted">Link verified</span>
        ) : (
          <span className="bj-muted bj-small">Not yet link-verified</span>
        )}
        <a href={job.apply_url} target="_blank" rel="noreferrer">
          View posting
        </a>
        {trackedApplicationId ? (
          <Link to={`/applications/${trackedApplicationId}`}>Open workspace</Link>
        ) : (
          <button onClick={() => onTrack(job)} disabled={tracking}>
            {tracking ? "Tracking..." : "Track"}
          </button>
        )}
      </div>
    </div>
  );
}
