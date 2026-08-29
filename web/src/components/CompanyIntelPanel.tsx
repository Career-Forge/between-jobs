import { useCallback, useEffect, useState } from "react";
import { apiFetch, ApiError } from "../lib/api";

// Company Intel panel (Horizon Sprint 5.0) -- Proposal §26, §37.4
// ("Company intelligence... a contextual capability inside the
// application," not a top-level page). Shows sourced claims only --
// every claim carries the real URL it came from, never bare prose,
// matching §26's own "a dossier consists of claims, not one opaque
// Markdown blob."

type Confidence = "high" | "medium" | "low";

interface Claim {
  id: string;
  category: string;
  claim_text: string;
  source_url: string;
  source_title: string | null;
  confidence: Confidence;
}

interface Run {
  id: string;
  company_name: string;
  providers_used: string[];
  warnings: string[];
  created_at: string;
}

interface CompanyIntelResponse {
  run: Run | null;
  claims: Claim[];
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; run: Run | null; claims: Claim[] };

const CATEGORY_LABELS: Record<string, string> = {
  product_and_mission: "Product & mission",
  team_and_technical_direction: "Team & technical direction",
  funding_and_financial_health: "Funding & financial health",
  hiring_activity: "Hiring activity",
  layoffs_and_risk: "Layoffs & risk",
  culture_and_values: "Culture & values",
  interview_process: "Interview process",
  relevant_news: "Relevant news",
};

function confidenceBadgeClass(confidence: Confidence): string {
  switch (confidence) {
    case "high":
      return "bj-badge-emerald";
    case "medium":
      return "bj-badge-gold";
    case "low":
      return "bj-badge-muted";
  }
}

export function CompanyIntelPanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const result = await apiFetch<CompanyIntelResponse>(
        `/applications/${applicationId}/company-intel`,
      );
      setState({ kind: "ready", run: result.run, claims: result.claims });
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
      const result = await apiFetch<CompanyIntelResponse>(
        `/applications/${applicationId}/company-intel`,
        { method: "POST" },
      );
      setState({ kind: "ready", run: result.run, claims: result.claims });
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
    <div className="bj-card bj-company-intel-panel">
      <h2>Company intel</h2>
      <p className="bj-muted bj-small">
        Sourced facts only -- every claim below links to where it came from. Runs real You.com/
        Firecrawl searches plus one LLM call each time, using your own keys.
      </p>
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => void generate()} disabled={generating}>
          {generating ? "Researching..." : state.kind === "ready" && state.run ? "Refresh" : "Generate"}
        </button>
      </div>

      {state.kind === "loading" && <div className="bj-muted bj-small">Loading...</div>}
      {state.kind === "error" && <div className="bj-error">{state.message}</div>}

      {state.kind === "ready" && state.run === null && (
        <div className="bj-muted bj-small">No dossier yet -- generate one above.</div>
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
          {state.claims.length === 0 ? (
            <div className="bj-muted bj-small">
              No claims came back from that research pass -- try refreshing.
            </div>
          ) : (
            <div className="bj-coverage-list">
              {state.claims.map((claim) => (
                <div key={claim.id} className="bj-coverage-evidence">
                  <div className="bj-coverage-name">
                    {CATEGORY_LABELS[claim.category] ?? claim.category}
                  </div>
                  <div>{claim.claim_text}</div>
                  <div className="bj-actions">
                    <span className={confidenceBadgeClass(claim.confidence)}>
                      {claim.confidence}
                    </span>
                    <a href={claim.source_url} target="_blank" rel="noreferrer" className="bj-small">
                      {claim.source_title || claim.source_url}
                    </a>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
