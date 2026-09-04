import { useCallback, useEffect, useState } from "react";
import { apiFetch, ApiError } from "../lib/api";

// Events warm-path panel (outreach-contactfinder.md Phase D) -- Proposal
// §27.5 / MASTER_PLAN §5.10b, "the best idea of the session." Three-state
// honesty is the point: a confirmed speaker is a much stronger signal
// than a likely-staffed sponsor booth, which is stronger than a merely
// company-adjacent event -- never collapsed into one undifferentiated
// list. On-demand for now; the proactive "notify me when a nearby event
// comes up" trigger is a disclosed, later follow-up (see
// warm_path_events.py's own module docstring).

type Certainty = "confirmed_speaker" | "likely_staffed_sponsor" | "company_adjacent";

interface WarmPathEvent {
  id: string;
  event_name: string;
  event_url: string;
  event_date: string | null;
  location: string | null;
  certainty: Certainty;
  speaker_name: string | null;
  speaker_title: string | null;
  talk_topic: string | null;
  source_title: string;
  source_snippet: string;
}

interface Run {
  id: string;
  company_name: string;
  providers_used: string[];
  warnings: string[];
  created_at: string;
}

interface WarmPathEventsResponse {
  run: Run | null;
  events: WarmPathEvent[];
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; run: Run | null; events: WarmPathEvent[] };

const CERTAINTY_LABELS: Record<Certainty, string> = {
  confirmed_speaker: "Confirmed speaker",
  likely_staffed_sponsor: "Likely-staffed sponsor",
  company_adjacent: "Company-adjacent",
};

function certaintyBadgeClass(certainty: Certainty): string {
  switch (certainty) {
    case "confirmed_speaker":
      return "bj-badge-emerald";
    case "likely_staffed_sponsor":
      return "bj-badge-gold";
    case "company_adjacent":
      return "bj-badge-muted";
  }
}

export function WarmPathEventsPanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const result = await apiFetch<WarmPathEventsResponse>(
        `/applications/${applicationId}/warm-path-events`,
      );
      setState({ kind: "ready", run: result.run, events: result.events });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate() {
    setGenerating(true);
    try {
      const result = await apiFetch<WarmPathEventsResponse>(
        `/applications/${applicationId}/warm-path-events`,
        { method: "POST" },
      );
      setState({ kind: "ready", run: result.run, events: result.events });
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setState({ kind: "error", message: `${e.message} Add a key in Integrations.` });
        return;
      }
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to generate" });
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="bj-card bj-warm-path-events-panel">
      <h2>Warm-path events</h2>
      <p className="bj-muted bj-small">
        Public conference/meetup/sponsor listings near you tied to this company -- labeled
        honestly by certainty, never overstated. Attendees are never guessed; only a confirmed
        speaker is named.
      </p>
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => void generate()} disabled={generating}>
          {generating
            ? "Searching..."
            : state.kind === "ready" && state.run
              ? "Refresh"
              : "Find events"}
        </button>
      </div>

      {state.kind === "loading" && <div className="bj-muted bj-small">Loading...</div>}
      {state.kind === "error" && <div className="bj-error">{state.message}</div>}

      {state.kind === "ready" && state.run === null && (
        <div className="bj-muted bj-small">No events found yet -- run a search above.</div>
      )}

      {state.kind === "ready" && state.run !== null && (
        <>
          {state.run.warnings.length > 0 && (
            <ul className="bj-small bj-generate-warnings">
              {state.run.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {state.events.length === 0 ? (
            <div className="bj-muted bj-small">
              No nearby events turned up in that search pass -- try refreshing.
            </div>
          ) : (
            <div className="bj-coverage-list">
              {state.events.map((event) => (
                <div key={event.id} className="bj-coverage-evidence">
                  <div className="bj-coverage-name">{event.event_name}</div>
                  <div className="bj-muted bj-small">
                    {[event.event_date, event.location].filter(Boolean).join(" -- ")}
                  </div>
                  <div className="bj-actions">
                    <span className={certaintyBadgeClass(event.certainty)}>
                      {CERTAINTY_LABELS[event.certainty]}
                    </span>
                  </div>
                  {event.speaker_name && (
                    <div className="bj-small">
                      {event.speaker_name}
                      {event.speaker_title ? ` -- ${event.speaker_title}` : ""}
                      {event.talk_topic ? `: "${event.talk_topic}"` : ""}
                    </div>
                  )}
                  <a
                    href={event.event_url}
                    target="_blank"
                    rel="noreferrer"
                    className="bj-small"
                  >
                    {event.source_title || event.event_url}
                  </a>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
