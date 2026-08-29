import { useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, apiFetchBlob } from "../lib/api";
import type { ChecklistItem, PrepareApplicationResult } from "../lib/generateTypes";
import { GapInterview } from "./GapInterview";
import { HonestFloor } from "./HonestFloor";
import { ScoreBreakdown } from "./ScoreBreakdown";

// R5 (resumeforge-shape-and-fit.md): pin-conflict messages ride the same
// generic `warnings: string[]` channel as everything else (no new backend
// field) -- detected here by the exact prefix forge-engines' allocator
// generates (`pinWarnings` in allocator.py) and broken out into their own,
// more prominent banner rather than blending into the plain warnings list.
function isPinWarning(w: string): boolean {
  return w.startsWith("pinned item");
}

// C4 (coverforge-port.md): same "same shared warnings channel, split out by
// a plain prefix" pattern as isPinWarning above -- the prefix is
// forge_engines.claim_verify.flagged_claim_warnings' own "unsupported
// claim (...)" format. Never blocks or auto-edits anything (Pranav's
// explicit call, see the plan doc's C4 section) -- purely a visible flag.
function isClaimWarning(w: string): boolean {
  return w.startsWith("unsupported claim");
}

// Generate + export (Sprint 3.3f) -- wires Sprint 3.0e's own
// `POST /{id}/prepare` endpoint into the workspace for the first time,
// plus PDF download (compiled on demand via latex-service, Sprint 3.2e's
// renderer decision) and the export checklist (§24.5.7).
//
// The v12 plan's own wording for this sub-sprint also asks for "grounded
// patch UI (per-bullet evidence display, apply/revert, introduced-facts
// flags)" -- deliberately NOT built here. `PrepareApplicationResult
// .evidence_fact_ids` is always empty today: forge-engines' Pass1 selects
// achievements by ids it generates internally during ingest, which don't
// map onto this platform's career_fact ids (prepare_application's own
// docstring in applications_routes.py flags this as an open gap). A
// per-bullet UI with no real evidence behind it would be decoration, not
// a feature -- it waits for that gap to close.

type GenerateState =
  | { kind: "idle" }
  | { kind: "generating" }
  | { kind: "ready"; result: PrepareApplicationResult }
  | { kind: "error"; message: string };

type ChecklistState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; items: ChecklistItem[] }
  | { kind: "error"; message: string };

function checklistBadgeClass(status: ChecklistItem["status"]): string {
  switch (status) {
    case "pass":
      return "bj-badge-emerald";
    case "fail":
      return "bj-badge-danger";
    case "not_checked":
      return "bj-badge-muted";
  }
}

function checklistStatusLabel(status: ChecklistItem["status"]): string {
  switch (status) {
    case "pass":
      return "Pass";
    case "fail":
      return "Fail";
    case "not_checked":
      return "Not checked";
  }
}

export function GeneratePanel({
  applicationId,
  initialHasResume,
}: {
  applicationId: string;
  initialHasResume: boolean;
}) {
  const [generate, setGenerate] = useState<GenerateState>({ kind: "idle" });
  const [checklist, setChecklist] = useState<ChecklistState>({ kind: "idle" });
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [wantCoverLetter, setWantCoverLetter] = useState(false);
  const [downloadingCoverLetter, setDownloadingCoverLetter] = useState(false);

  async function runGenerate(forceGenerate = false) {
    setGenerate({ kind: "generating" });
    setChecklist({ kind: "idle" });
    setDownloadError(null);
    try {
      const result = await apiFetch<PrepareApplicationResult>(
        `/applications/${applicationId}/prepare`,
        {
          method: "POST",
          body: JSON.stringify({
            idempotency_key: crypto.randomUUID(),
            force_generate: forceGenerate,
            generate_cover_letter: wantCoverLetter,
          }),
        },
      );
      setGenerate({ kind: "ready", result });
    } catch (e) {
      setGenerate({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to generate",
      });
    }
  }

  async function downloadPdf() {
    setDownloading(true);
    setDownloadError(null);
    try {
      const blob = await apiFetchBlob(`/applications/${applicationId}/resume.pdf`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "resume.pdf";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : "Failed to download");
    } finally {
      setDownloading(false);
    }
  }

  // C1/C2 (coverforge-port.md): opt-in per generation, not a persisted
  // shape_overrides-style setting -- it's a "do I want one for THIS run"
  // choice (closer to force_generate's own one-off nature) rather than a
  // standing rendering preference, and it costs real extra LLM spend, so it
  // defaults off. The button below only appears after a fresh generation
  // in THIS session returns one (`generate.result.cover_letter`) -- unlike
  // `hasResume`, there's no `initialHasCoverLetter` persisted-across-reload
  // equivalent yet; deliberately out of this sprint's scope.
  async function downloadCoverLetter() {
    setDownloadingCoverLetter(true);
    setDownloadError(null);
    try {
      const blob = await apiFetchBlob(`/applications/${applicationId}/cover-letter.pdf`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "cover-letter.pdf";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : "Failed to download");
    } finally {
      setDownloadingCoverLetter(false);
    }
  }

  async function loadChecklist() {
    setChecklist({ kind: "loading" });
    try {
      const response = await apiFetch<{ items: ChecklistItem[] }>(
        `/applications/${applicationId}/export-checklist`,
      );
      setChecklist({ kind: "ready", items: response.items });
    } catch (e) {
      setChecklist({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to load checklist",
      });
    }
  }

  const hasResume =
    initialHasResume || (generate.kind === "ready" && generate.result.resume !== null);
  const hasCoverLetter = generate.kind === "ready" && !!generate.result.cover_letter;

  return (
    <div className="bj-card bj-generate-panel">
      <h2>Generate</h2>
      <p className="bj-muted bj-small">
        Runs the resume engine against this job -- a real LLM call, using your configured
        provider credential.
      </p>
      <div className="bj-actions">
        <button
          className="bj-primary"
          onClick={() => void runGenerate()}
          disabled={generate.kind === "generating"}
        >
          {generate.kind === "generating" ? "Generating..." : "Generate resume"}
        </button>
      </div>
      <label className="bj-section-editor-toggle bj-small">
        <input
          type="checkbox"
          checked={wantCoverLetter}
          onChange={(e) => setWantCoverLetter(e.target.checked)}
          disabled={generate.kind === "generating"}
        />
        Also generate a cover letter (a real, extra LLM call)
      </label>

      {generate.kind === "error" && <div className="bj-error">{generate.message}</div>}

      {generate.kind === "ready" && (
        <div className="bj-generate-result">
          {generate.result.resume === null ? (
            generate.result.fit ? (
              // S4c: the gate's own decline reason is already folded into
              // `warnings` (prepare_orchestrator.py) purely as plain text --
              // HonestFloor supersedes that with the full honest read, so
              // the warnings list below is deliberately skipped in this
              // branch rather than repeating the same reason twice.
              <HonestFloor
                applicationId={applicationId}
                fit={generate.result.fit}
                onGenerateAnyway={() => void runGenerate(true)}
              />
            ) : (
              <div className="bj-muted bj-small">
                The engine didn't produce a resume for this job.
              </div>
            )
          ) : (
            <>
              <ScoreBreakdown
                applicationId={applicationId}
                attempts={generate.result.ats_attempts}
              />
              <GapInterview
                applicationId={applicationId}
                attempts={generate.result.ats_attempts}
                onRegenerate={() => void runGenerate()}
              />
              {(() => {
                const pinWarnings = generate.result.warnings.filter(isPinWarning);
                const claimWarnings = generate.result.warnings.filter(isClaimWarning);
                const otherWarnings = generate.result.warnings.filter(
                  (w) => !isPinWarning(w) && !isClaimWarning(w),
                );
                return (
                  <>
                    {pinWarnings.length > 0 && (
                      <div className="bj-pin-conflict-banner">
                        <div>📌 Some mandatory entries didn't fully fit:</div>
                        <ul className="bj-small">
                          {pinWarnings.map((w) => (
                            <li key={w}>{w}</li>
                          ))}
                        </ul>
                        <div className="bj-small">
                          Every pinned entry is still included -- only its bullet count fell
                          short. <Link to="/profile">Edit profile</Link> to unpin an entry or
                          lower its minimum. (Page-count and density overrides are coming in
                          a later update.)
                        </div>
                      </div>
                    )}
                    {claimWarnings.length > 0 && (
                      <div className="bj-claim-warning-banner">
                        <div>⚠️ Claim verification flagged {claimWarnings.length} claim(s):</div>
                        <ul className="bj-small">
                          {claimWarnings.map((w) => (
                            <li key={w}>{w}</li>
                          ))}
                        </ul>
                        <div className="bj-small">
                          Nothing was auto-edited or blocked -- review these against your own
                          background before you send anything.
                        </div>
                      </div>
                    )}
                    {otherWarnings.length > 0 && (
                      <ul className="bj-small bj-generate-warnings">
                        {otherWarnings.map((w) => (
                          <li key={w}>{w}</li>
                        ))}
                      </ul>
                    )}
                  </>
                );
              })()}
            </>
          )}
        </div>
      )}

      {hasResume && (
        <div className="bj-actions">
          <button onClick={() => void downloadPdf()} disabled={downloading}>
            {downloading ? "Compiling PDF..." : "Download PDF"}
          </button>
          {hasCoverLetter && (
            <button onClick={() => void downloadCoverLetter()} disabled={downloadingCoverLetter}>
              {downloadingCoverLetter ? "Compiling PDF..." : "Download cover letter"}
            </button>
          )}
          <button onClick={() => void loadChecklist()} disabled={checklist.kind === "loading"}>
            {checklist.kind === "loading" ? "Checking..." : "Export checklist"}
          </button>
        </div>
      )}
      {downloadError && <div className="bj-error">{downloadError}</div>}

      {checklist.kind === "error" && <div className="bj-error">{checklist.message}</div>}
      {checklist.kind === "ready" && (
        <ul className="bj-checklist">
          {checklist.items.map((item) => (
            <li key={item.key} className="bj-checklist-item">
              <span className={checklistBadgeClass(item.status)}>
                {checklistStatusLabel(item.status)}
              </span>
              <div>
                <div className="bj-checklist-label">{item.label}</div>
                <div className="bj-muted bj-small">{item.detail}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
