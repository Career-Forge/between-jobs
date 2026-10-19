import { useId, useState } from "react";
import { countWords, intelConfidence, runDate } from "../lib/research";
import { intelTrail, failedTrail } from "../lib/researchTrails";
import { SETTINGS_PATH } from "../lib/settingsPaths";
import { useNow } from "../lib/useNow";
import { useResearchRun } from "../lib/useResearchRun";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState } from "./paper/StateViews";
import { EmptyBlock } from "./research/EmptyBlock";
import { EvidenceItem, EvidenceList } from "./research/Evidence";
import { RunButton, RunProblem, RunWarnings } from "./research/RunParts";
import "../styles/research.css";

// Company intel: what the web says about this company, as sourced claims -- never one block of prose. A
// run searches with the person's own You.com or Firecrawl key, then their model writes the claims from
// what came back; code keeps a claim only when its source is one of the results the search returned. So
// every claim carries a link, and a confidence that is the model's own reading (gold cells and the word,
// never green: it is not a checked fact).
//
// States:
//   loading    a placeholder in the dossier's shape while the stored run is read
//   empty      nothing has been run: what a run finds and what it needs, and a link to Settings
//   stale      a stored run is shown with its own date ("Researched Sep 20", from `created_at`)
//   partial    a run whose searches did not all complete says how many, in the server's words
//   setup      a run that needs a key says what, with the link that fixes it (no Retry)
//   failure    the stored run could not be read (Retry reads again), or a run failed (the earlier result
//              stays, and Retry makes the run again)
//
// A long dossier shows its first claims with "3 of 8 claims" and a button that opens the rest. The
// section's anchor never collapses: only the claims below the first few do.

interface Claim {
  id: string;
  category: string;
  claim_text: string;
  source_url: string;
  source_title: string | null;
  confidence: string;
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

// A dossier longer than this opens on its first few claims.
export const COLLAPSE_ABOVE = 5;
export const COLLAPSED_SHOWS = 3;

export function CompanyIntelPanel({ applicationId, now }: { applicationId: string; now?: Date }) {
  const clock = useNow(now);
  const { read, running, problem, load, run, runButton, focusRunButton } = useResearchRun<CompanyIntelResponse>(`/applications/${applicationId}/company-intel`, {
    read: "Failed to load",
    run: "Failed to generate",
  });
  const [expanded, setExpanded] = useState(false);
  const listId = useId();

  const ready = read.kind === "ready" ? read.data : null;
  const data = ready !== null && ready.run !== null ? { run: ready.run, claims: ready.claims } : null;
  const collapsed = data !== null && data.claims.length > COLLAPSE_ABOVE && !expanded;
  const shown = data === null ? [] : collapsed ? data.claims.slice(0, COLLAPSED_SHOWS) : data.claims;
  // The status region's words come from the run, not from whether the disclosure is open: opening or folding
  // the list must not read out a run summary again.
  const researched = data === null ? null : runDate(data.run, clock);
  const meta =
    data === null
      ? []
      : [researched === null ? null : `Researched ${researched}`, countWords(data.claims.length, "claim", "claims")].filter((part): part is string => part !== null);

  const trail =
    data !== null
      ? intelTrail(data.run, data.claims.length, clock)
      : read.kind === "failed"
        ? failedTrail("Company intel couldn't be read")
        : problem?.kind === "error"
          ? failedTrail("That research run didn't finish")
          : null;

  return (
    <div className="bj-rs">
      <p className="bj-ws-intro">
        Sourced claims about this company. Each one links to a page your search returned, and the confidence beside it is your model's own reading. A run searches with your You.com or Firecrawl key
        and calls your model to write the claims. When it finds claims about the interview process it calls your model once more, to build interview research that other people's practice sessions can
        use too. It uses your own keys each time.
      </p>
      <RunButton
        label={data !== null ? "Refresh" : "Research this company"}
        busyLabel="Researching..."
        busy={running}
        held={read.kind === "loading"}
        buttonRef={runButton}
        onRun={() => {
          if (read.kind !== "loading") void run();
        }}
      />

      <div role="status" className="bj-rs-live">
        {meta.length > 0 && <p className="bj-rs-meta">{meta.join(" · ")}</p>}
      </div>

      {read.kind === "loading" && <LoadingState rows={3} rowHeight={72} label="Loading company intel..." />}
      {read.kind === "failed" && <FailureState message={read.message} onRetry={() => void load()} restoreFocusTo={focusRunButton} />}
      <RunProblem problem={problem} onRetry={() => void run()} restoreFocusTo={focusRunButton} />

      {ready !== null && data === null && (
        <EmptyBlock heading="No dossier yet" link={{ to: SETTINGS_PATH, label: "Keys are added in Settings" }}>
          <p>
            A run collects what this company says about itself and what the press says, as claims you can check: product and mission, team and technical direction, funding, hiring activity, layoffs and
            risk, culture, interview process and news.
          </p>
          <p>It needs a model key and a You.com or Firecrawl key.</p>
        </EmptyBlock>
      )}

      {data !== null && (
        <>
          <RunWarnings run={data.run} />
          {data.claims.length === 0 ? (
            <p className="bj-muted bj-small">No claims came back from that research pass. Refresh to try again.</p>
          ) : (
            <>
              <ul id={listId} className="bj-rs-list" aria-label="Company claims">
                {shown.map((claim) => (
                  <li key={claim.id} className="bj-rs-item">
                    <p className="bj-rs-eyebrow">{CATEGORY_LABELS[claim.category] ?? claim.category}</p>
                    <p className="bj-rs-text">{claim.claim_text}</p>
                    <EvidenceList label="Source and confidence">
                      <EvidenceItem url={claim.source_url} title={claim.source_title} confidence={intelConfidence(claim.confidence)} />
                    </EvidenceList>
                  </li>
                ))}
              </ul>
              {data.claims.length > COLLAPSE_ABOVE && (
                <div className="bj-rs-more">
                  {collapsed && <span className="bj-rs-meta">{`${shown.length} of ${data.claims.length} claims`}</span>}
                  <button type="button" aria-expanded={!collapsed} aria-controls={listId} onClick={() => setExpanded((open) => !open)}>
                    {collapsed ? `Show all ${data.claims.length}` : "Show fewer"}
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}

      {trail !== null && <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />}
    </div>
  );
}
