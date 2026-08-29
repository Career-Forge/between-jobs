import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, ApiError } from "../lib/api";

// Discover (Horizon Sprint 4.1) -- a read-only search over n8n's own
// live job registry (Proposal §23, scoped per the v12 plan's own words:
// a facade reading n8n's Postgres, not a native port of its poller/
// registry/ranking pipeline). "Track" reuses the same manual-paste job
// ingestion the Applications page and Telegram's job-paste flow already
// use -- one write path, three ways to reach it.

interface DiscoverJob {
  id: number;
  title: string;
  company_name: string;
  location: string | null;
  remote: boolean | null;
  apply_url: string;
  posted_at: string | null;
}

type State =
  | { kind: "loading" }
  | { kind: "not_configured" }
  | { kind: "error"; message: string }
  | { kind: "ready"; jobs: DiscoverJob[] };

export default function Discover() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [query, setQuery] = useState("");
  const [tracking, setTracking] = useState<number | null>(null);
  const [tracked, setTracked] = useState<Record<number, string>>({});
  const [trackError, setTrackError] = useState<string | null>(null);

  const search = useCallback(async (q: string) => {
    setState({ kind: "loading" });
    try {
      const jobs = await apiFetch<DiscoverJob[]>(
        `/discover${q ? `?q=${encodeURIComponent(q)}` : ""}`,
      );
      setState({ kind: "ready", jobs });
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setState({ kind: "not_configured" });
        return;
      }
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to search" });
    }
  }, []);

  useEffect(() => {
    void search("");
  }, [search]);

  async function track(job: DiscoverJob) {
    setTracking(job.id);
    setTrackError(null);
    try {
      const application = await apiFetch<{ id: string }>(`/discover/${job.id}/track`, {
        method: "POST",
      });
      setTracked((prev) => ({ ...prev, [job.id]: application.id }));
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
            onKeyDown={(e) => e.key === "Enter" && void search(query)}
          />
        </label>
        <div className="bj-actions">
          <button className="bj-primary" onClick={() => void search(query)}>
            Search
          </button>
        </div>
      </div>

      {state.kind === "loading" && <div className="bj-muted bj-small">Loading...</div>}

      {state.kind === "not_configured" && (
        <div className="bj-empty">
          <h2>Job discovery isn't configured on this server</h2>
          <p>
            Discover reads a live job registry this deployment hasn't connected yet. Nothing
            fake shows here in the meantime.
          </p>
        </div>
      )}

      {state.kind === "error" && (
        <div>
          <div className="bj-error">{state.message}</div>
          <button onClick={() => void search(query)}>Retry</button>
        </div>
      )}

      {state.kind === "ready" && state.jobs.length === 0 && (
        <div className="bj-empty">
          <h2>No results</h2>
          <p>Try a different search.</p>
        </div>
      )}

      {state.kind === "ready" && state.jobs.length > 0 && (
        <div className="bj-today-list">
          {trackError && <div className="bj-error">{trackError}</div>}
          {state.jobs.map((job) => (
            <div key={job.id} className="bj-card bj-today-item">
              <div className="bj-today-item-main">
                <div>
                  <div>
                    {job.title} @ {job.company_name}
                  </div>
                  <div className="bj-muted bj-small">
                    {job.location ?? "Location unknown"}
                    {job.remote ? " -- Remote" : ""}
                  </div>
                </div>
              </div>
              {tracked[job.id] ? (
                <Link to={`/applications/${tracked[job.id]}`}>Open workspace</Link>
              ) : (
                <button onClick={() => void track(job)} disabled={tracking === job.id}>
                  {tracking === job.id ? "Tracking..." : "Track"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
