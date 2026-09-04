import { useCallback, useEffect, useState } from "react";
import { apiFetch, ApiError } from "../lib/api";

// "Your play" positioning brief (outreach-v2-search-first.md Phase I) --
// Proposal §27.6's "gap-analysis sleeper feature." Every sentence here
// traces back to something this platform already deterministically
// computed (Tailor's live coverage/skill-state read, Company Intel
// claims) -- never a re-derived or guessed verdict. Same "no data, no
// bogus advice" honesty as HonestFloor/WarmPathEventsPanel: a below-
// evidence application shows a plain explanation, not a hallucinated
// brief.

interface PositioningBrief {
  id: string;
  lead_with: string;
  lead_with_citation: string;
  gap_that_matters: string;
  gap_citation: string;
  recommended_project: string;
  rubric_warnings: string[];
  created_at: string;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "empty" }
  | { kind: "ready"; brief: PositioningBrief };

export function PositioningBriefPanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const result = await apiFetch<{ brief: PositioningBrief | null }>(
        `/applications/${applicationId}/positioning-brief`,
      );
      setState(result.brief ? { kind: "ready", brief: result.brief } : { kind: "empty" });
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
      const result = await apiFetch<{ brief: PositioningBrief }>(
        `/applications/${applicationId}/positioning-brief`,
        { method: "POST" },
      );
      setState({ kind: "ready", brief: result.brief });
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setState({ kind: "error", message: `${e.message} Add a key in Integrations.` });
        return;
      }
      if (e instanceof ApiError && e.code === "INSUFFICIENT_EVIDENCE") {
        setState({
          kind: "error",
          message: `${e.message} Try again once your profile or this role's requirements are clearer.`,
        });
        return;
      }
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to generate" });
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="bj-card bj-positioning-brief-panel">
      <h2>Your play</h2>
      <p className="bj-muted bj-small">
        What to lead with, the one gap that actually matters for this role, and one project idea
        to close it -- every claim traced to a real, already-computed signal, never a guess.
      </p>
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => void generate()} disabled={generating}>
          {generating
            ? "Building..."
            : state.kind === "ready"
              ? "Refresh"
              : "Build my positioning brief"}
        </button>
      </div>

      {state.kind === "loading" && <div className="bj-muted bj-small">Loading...</div>}
      {state.kind === "error" && <div className="bj-error">{state.message}</div>}
      {state.kind === "empty" && (
        <div className="bj-muted bj-small">No brief yet -- build one above.</div>
      )}

      {state.kind === "ready" && (
        <>
          {state.brief.rubric_warnings.length > 0 && (
            <ul className="bj-small bj-generate-warnings">
              {state.brief.rubric_warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <div className="bj-honest-floor-section">
            <span className="bj-honest-floor-section-label">Lead with</span>
            <p className="bj-small">{state.brief.lead_with}</p>
            <span className="bj-badge-emerald">{state.brief.lead_with_citation}</span>
          </div>
          <div className="bj-honest-floor-section">
            <span className="bj-honest-floor-section-label">The gap that matters</span>
            <p className="bj-small">{state.brief.gap_that_matters}</p>
            <span className="bj-badge-danger">{state.brief.gap_citation}</span>
          </div>
          <div className="bj-honest-floor-section">
            <span className="bj-honest-floor-section-label">One project to close it</span>
            <p className="bj-small">{state.brief.recommended_project}</p>
          </div>
        </>
      )}
    </div>
  );
}
