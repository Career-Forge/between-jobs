import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "../lib/api";
import { friendlyApiMessage } from "../lib/rateLimitMessage";
import { briefTrail, failedTrail } from "../lib/researchTrails";
import { failureOf, type Failure } from "../lib/setupRequired";
import { formatTrailDate } from "../lib/trail";
import { useNow } from "../lib/useNow";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState, PartialState } from "./paper/StateViews";
import { EmptyBlock } from "./research/EmptyBlock";
import { CitationItem, EvidenceList, EvidenceNote } from "./research/Evidence";
import { SetupRequiredNotice } from "./SetupRequiredNotice";
import "../styles/workspace.css";
import "../styles/research.css";

// "Your play": what to lead with, the one gap that matters for this role, and one project to close it.
// Every sentence is the person's model's, written from signals the platform already computed (Tailor's
// coverage read, and Company intel when it was run), and each of the first two carries a citation that
// code checked is one of the strengths or gaps found for this role. A citation is evidence, not a
// verdict: it is gold, and the gap's is gold too, never red.
//
// A model that cannot ground a brief is not made to: the server answers INSUFFICIENT_EVIDENCE with its
// reason (nothing supported to lead with, no gap left to name, or its last reply failed the parse or the
// citation check), and that is a gold note, not a failure. A brief the code flagged for outcome-promise
// wording is shown with each flag as a neutral outline note above it; nothing is removed.
//
// States: a placeholder while the stored brief is read; empty teaching before the first build; the brief
// with the day it was built; a build in flight keeps the brief already shown and busies only its button;
// a failed build keeps it too, with the failure beside it and a Retry; a missing key or profile is a
// notice with the link that fixes it and no Retry.

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

type Read = { kind: "loading" } | { kind: "failed"; message: string } | { kind: "ready"; brief: PositioningBrief | null };

type Outcome = Failure | { kind: "note"; message: string };

export function PositioningBriefPanel({ applicationId, now }: { applicationId: string; now?: Date }) {
  const clock = useNow(now);
  const [read, setRead] = useState<Read>({ kind: "loading" });
  const [building, setBuilding] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const working = useRef(false);
  const buildButton = useRef<HTMLButtonElement>(null);
  // Retry removes the callout it is pressed in; focus goes on to the build button, which stays.
  const focusBuildButton = useCallback(() => buildButton.current, []);

  const load = useCallback(async () => {
    setRead({ kind: "loading" });
    setOutcome(null);
    try {
      const result = await apiFetch<{ brief: PositioningBrief | null }>(`/applications/${applicationId}/positioning-brief`);
      setRead({ kind: "ready", brief: result.brief });
    } catch (e) {
      setRead({ kind: "failed", message: friendlyApiMessage(e, "Failed to load") });
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function build() {
    if (working.current || read.kind === "loading") return;
    working.current = true;
    setBuilding(true);
    setOutcome(null);
    try {
      const result = await apiFetch<{ brief: PositioningBrief }>(`/applications/${applicationId}/positioning-brief`, { method: "POST" });
      setRead({ kind: "ready", brief: result.brief });
    } catch (e) {
      // Not enough to ground a brief is the server's honest answer, not a failure: its reason, as a note.
      if (e instanceof ApiError && e.code === "INSUFFICIENT_EVIDENCE") setOutcome({ kind: "note", message: e.message });
      else setOutcome(failureOf(e, "Failed to generate"));
    } finally {
      working.current = false;
      setBuilding(false);
    }
  }

  const brief = read.kind === "ready" ? read.brief : null;
  const built = brief === null ? null : formatTrailDate(brief.created_at, { now: clock });
  const trail = brief !== null ? briefTrail(brief, clock) : read.kind === "failed" ? failedTrail("Your play couldn't be read") : outcome?.kind === "error" ? failedTrail("That brief didn't finish") : null;

  return (
    <div className="bj-rs">
      <p className="bj-ws-intro">
        What to lead with, the one gap that matters for this role, and one project to close it. Written by your model on your key from signals already computed for this role: your profile's match to the
        posting and, if you ran it, Company intel.
      </p>
      <div className="bj-actions bj-rs-bar">
        <button ref={buildButton} type="button" aria-disabled={building || read.kind === "loading" ? true : undefined} onClick={() => void build()}>
          {building ? "Building..." : brief !== null ? "Refresh" : "Build my positioning brief"}
        </button>
      </div>

      <div role="status" className="bj-rs-live">
        {built !== null && <p className="bj-rs-meta">Built {built}</p>}
      </div>

      {read.kind === "loading" && <LoadingState rows={3} rowHeight={64} label="Loading your brief..." />}
      {read.kind === "failed" && <FailureState message={read.message} onRetry={() => void load()} restoreFocusTo={focusBuildButton} />}

      {/* The status region is on the page before the note, so the note's words are announced when they arrive. */}
      <div role="status" className="bj-rs-live">
        {outcome !== null && outcome.kind === "note" && <EvidenceNote announce={false}>{outcome.message}</EvidenceNote>}
      </div>
      {outcome !== null &&
        (outcome.kind === "setup" ? (
          <SetupRequiredNotice notice={outcome.notice} />
        ) : outcome.kind === "error" ? (
          <FailureState message={outcome.message} onRetry={() => void build()} restoreFocusTo={focusBuildButton} />
        ) : null)}

      {read.kind === "ready" && brief === null && (
        <EmptyBlock heading="No brief yet">
          <p>
            A brief has three parts: what to lead with, backed by a strength your profile shows; the one gap that matters for this role; and one project that would close it. Each of the first two cites the
            strength or gap it rests on.
          </p>
          <p>It needs a model key and an active profile.</p>
        </EmptyBlock>
      )}

      {brief !== null && (
        <>
          {brief.rubric_warnings.length > 0 && <PartialState lead="A code check flagged this brief:" notices={brief.rubric_warnings} />}
          <div className="bj-rs-brief">
            <section className="bj-rs-part" aria-labelledby={`${applicationId}-lead`}>
              <h3 id={`${applicationId}-lead`} className="bj-rs-label">
                Lead with
              </h3>
              <p className="bj-rs-text">{brief.lead_with}</p>
              <EvidenceList label="Evidence for what to lead with">
                <CitationItem>{brief.lead_with_citation}</CitationItem>
              </EvidenceList>
            </section>
            <section className="bj-rs-part" aria-labelledby={`${applicationId}-gap`}>
              <h3 id={`${applicationId}-gap`} className="bj-rs-label">
                The gap that matters
              </h3>
              <p className="bj-rs-text">{brief.gap_that_matters}</p>
              <EvidenceList label="Evidence for the gap">
                <CitationItem>{brief.gap_citation}</CitationItem>
              </EvidenceList>
            </section>
            <section className="bj-rs-part" aria-labelledby={`${applicationId}-project`}>
              <h3 id={`${applicationId}-project`} className="bj-rs-label">
                One project to close it
              </h3>
              <p className="bj-rs-text">{brief.recommended_project}</p>
            </section>
          </div>
        </>
      )}

      {trail !== null && <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />}
    </div>
  );
}
