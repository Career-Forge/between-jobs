import { useCallback, useEffect, useState } from "react";
import { apiFetch, ApiError } from "../lib/api";

// ContactFinder panel (outreach-contactfinder.md Phase B/C) -- Proposal
// §27, §37.4 ("Company Intel and ContactFinder are not competing
// top-level products. They are contextual capabilities inside the
// application"). Discovery never surfaces a bare name, matching the same
// "claims, not one opaque blob" discipline CompanyIntelPanel already
// established. Enrichment (Phase C) is opt-in, one candidate at a time,
// gated behind an explicit confirm dialog naming what will happen --
// discovery itself never touches an email or phone number.

type Confidence = "verified" | "strong" | "inferred" | "unsupported";

interface Evidence {
  id: string;
  source_url: string;
  source_title: string;
  source_snippet: string;
  observed_at: string;
  evidence_kind: string;
  confidence: Confidence;
}

interface Candidate {
  id: string;
  person_name: string;
  claimed_title: string | null;
  claimed_team: string | null;
  persona: string;
  relevance_reason: string;
  priority_score: number;
  evidence: Evidence[];
  enriched_email: string | null;
  enriched_email_status: string | null;
  enrichment_provider: string | null;
  // outreach-v2-search-first.md Phase I: literally the same hook
  // outreach_writer.build_hook_context would pick for this candidate --
  // null only when the candidate has no evidence to hook from.
  approach_hint: string | null;
}

interface OutreachDraft {
  id: string;
  subject: string;
  email_body: string;
  linkedin_message: string;
  follow_up_message: string;
  rubric_warnings: string[];
  gmail_draft_id: string | null;
}

interface Run {
  id: string;
  company_name: string;
  providers_used: string[];
  warnings: string[];
  created_at: string;
}

interface ContactsResponse {
  run: Run | null;
  candidates: Candidate[];
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; run: Run | null; candidates: Candidate[] };

const PERSONA_LABELS: Record<string, string> = {
  hiring_lead: "Hiring lead",
  recruiter: "Recruiter",
  manager: "Engineering manager",
  senior_leader: "Senior leader",
  senior_ic: "Senior IC",
};

function confidenceBadgeClass(confidence: Confidence): string {
  switch (confidence) {
    case "verified":
      return "bj-badge-emerald";
    case "strong":
      return "bj-badge-gold";
    case "inferred":
      return "bj-badge-muted";
    case "unsupported":
      return "bj-badge-muted";
  }
}

export function ContactFinderPanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [generating, setGenerating] = useState(false);
  const [enrichingId, setEnrichingId] = useState<string | null>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);
  const [draftingId, setDraftingId] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, OutreachDraft>>({});
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [pushingId, setPushingId] = useState<string | null>(null);
  const [pushError, setPushError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const result = await apiFetch<ContactsResponse>(`/applications/${applicationId}/contacts`);
      setState({ kind: "ready", run: result.run, candidates: result.candidates });
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
      const result = await apiFetch<ContactsResponse>(`/applications/${applicationId}/contacts`, {
        method: "POST",
      });
      setState({ kind: "ready", run: result.run, candidates: result.candidates });
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

  async function enrich(candidate: Candidate) {
    const confirmed = window.confirm(
      `Look up a confirmed work email for ${candidate.person_name} via Apollo? This uses your ` +
        "own Apollo credit for one lookup -- never a bulk search, never personal contact info.",
    );
    if (!confirmed) return;

    setEnrichingId(candidate.id);
    setEnrichError(null);
    try {
      const updated = await apiFetch<Candidate>(
        `/applications/${applicationId}/contacts/${candidate.id}/enrich`,
        { method: "POST" },
      );
      setState((prev) =>
        prev.kind === "ready"
          ? { ...prev, candidates: prev.candidates.map((c) => (c.id === updated.id ? updated : c)) }
          : prev,
      );
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setEnrichError(`${e.message} Add an Apollo key in Integrations.`);
        return;
      }
      setEnrichError(e instanceof Error ? e.message : "Enrichment failed");
    } finally {
      setEnrichingId(null);
    }
  }

  async function draftOutreach(candidate: Candidate) {
    setDraftingId(candidate.id);
    setDraftError(null);
    try {
      const result = await apiFetch<{ draft: OutreachDraft }>(
        `/applications/${applicationId}/contacts/${candidate.id}/draft-outreach`,
        { method: "POST" },
      );
      setDrafts((prev) => ({ ...prev, [candidate.id]: result.draft }));
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setDraftError(`${e.message} Add a key in Integrations.`);
        return;
      }
      if (e instanceof ApiError && e.code === "INSUFFICIENT_EVIDENCE") {
        setDraftError(e.message);
        return;
      }
      setDraftError(e instanceof Error ? e.message : "Drafting failed");
    } finally {
      setDraftingId(null);
    }
  }

  async function copyText(field: string, text: string) {
    await navigator.clipboard.writeText(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  }

  async function pushToGmail(candidate: Candidate) {
    setPushingId(candidate.id);
    setPushError(null);
    try {
      const updated = await apiFetch<OutreachDraft>(
        `/applications/${applicationId}/contacts/${candidate.id}/push-to-gmail`,
        { method: "POST" },
      );
      setDrafts((prev) => ({ ...prev, [candidate.id]: updated }));
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setPushError(`${e.message} Connect Gmail in Integrations.`);
        return;
      }
      setPushError(e instanceof Error ? e.message : "Couldn't create that Gmail draft.");
    } finally {
      setPushingId(null);
    }
  }

  return (
    <div className="bj-card bj-contact-finder-panel">
      <h2>Contacts</h2>
      <p className="bj-muted bj-small">
        Publicly sourced people only -- every contact below links to where it came from. No
        emails or phone numbers here; that's a separate, opt-in step. Runs real You.com/Firecrawl
        searches plus one LLM call each time, using your own keys.
      </p>
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => void generate()} disabled={generating}>
          {generating
            ? "Researching..."
            : state.kind === "ready" && state.run
              ? "Refresh"
              : "Find contacts"}
        </button>
      </div>

      {state.kind === "loading" && <div className="bj-muted bj-small">Loading...</div>}
      {state.kind === "error" && <div className="bj-error">{state.message}</div>}
      {enrichError && <div className="bj-error">{enrichError}</div>}
      {draftError && <div className="bj-error">{draftError}</div>}
      {pushError && <div className="bj-error">{pushError}</div>}

      {state.kind === "ready" && state.run === null && (
        <div className="bj-muted bj-small">No contacts found yet -- run a search above.</div>
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
          {state.candidates.length === 0 ? (
            <div className="bj-muted bj-small">
              No named individuals turned up in that search pass -- try refreshing.
            </div>
          ) : (
            <div className="bj-coverage-list">
              {state.candidates.map((candidate) => (
                <div key={candidate.id} className="bj-coverage-evidence">
                  <div className="bj-coverage-name">
                    {candidate.person_name}
                    {candidate.claimed_title ? ` -- ${candidate.claimed_title}` : ""}
                  </div>
                  {candidate.claimed_team && (
                    <div className="bj-muted bj-small">{candidate.claimed_team}</div>
                  )}
                  <div className="bj-small">{candidate.relevance_reason}</div>
                  {candidate.approach_hint && (
                    <div className="bj-small">
                      <strong>Approach:</strong> {candidate.approach_hint}
                    </div>
                  )}
                  <div className="bj-actions">
                    <span className="bj-badge-muted">
                      {PERSONA_LABELS[candidate.persona] ?? candidate.persona}
                    </span>
                    <span className="bj-small">{Math.round(candidate.priority_score)}/100</span>
                  </div>
                  <ul className="bj-small">
                    {candidate.evidence.map((e) => (
                      <li key={e.id}>
                        <span className={confidenceBadgeClass(e.confidence)}>{e.confidence}</span>{" "}
                        <a href={e.source_url} target="_blank" rel="noreferrer">
                          {e.source_title || e.source_url}
                        </a>
                      </li>
                    ))}
                  </ul>
                  <div className="bj-actions">
                    {candidate.enriched_email ? (
                      <span className="bj-small">
                        {candidate.enriched_email}
                        {candidate.enriched_email_status ? ` (${candidate.enriched_email_status})` : ""}
                        {candidate.enrichment_provider ? ` via ${candidate.enrichment_provider}` : ""}
                      </span>
                    ) : (
                      <button
                        className="bj-primary"
                        onClick={() => void enrich(candidate)}
                        disabled={enrichingId === candidate.id}
                      >
                        {enrichingId === candidate.id ? "Looking up..." : "Find work email"}
                      </button>
                    )}
                    <button
                      className="bj-primary"
                      onClick={() => void draftOutreach(candidate)}
                      disabled={draftingId === candidate.id}
                    >
                      {draftingId === candidate.id
                        ? "Drafting..."
                        : drafts[candidate.id]
                          ? "Redraft outreach"
                          : "Draft outreach"}
                    </button>
                  </div>

                  {drafts[candidate.id] && (
                    <div className="bj-coverage-evidence">
                      {drafts[candidate.id].rubric_warnings.length > 0 && (
                        <ul className="bj-small bj-generate-warnings">
                          {drafts[candidate.id].rubric_warnings.map((w) => (
                            <li key={w}>{w}</li>
                          ))}
                        </ul>
                      )}
                      {(
                        [
                          ["subject", "Subject", drafts[candidate.id].subject],
                          ["email_body", "Email", drafts[candidate.id].email_body],
                          [
                            "linkedin_message",
                            "LinkedIn message",
                            drafts[candidate.id].linkedin_message,
                          ],
                          ["follow_up_message", "Follow-up", drafts[candidate.id].follow_up_message],
                        ] as const
                      ).map(([field, label, text]) => (
                        <div key={field} className="bj-small">
                          <strong>{label}:</strong>
                          <div>{text}</div>
                          <button
                            className="bj-link-button"
                            onClick={() => void copyText(`${candidate.id}-${field}`, text)}
                          >
                            {copiedField === `${candidate.id}-${field}` ? "Copied!" : "Copy"}
                          </button>
                        </div>
                      ))}
                      <p className="bj-muted bj-small">
                        Nothing is sent automatically -- copy whichever message you want and send
                        it yourself.
                      </p>
                      <div className="bj-actions">
                        {drafts[candidate.id].gmail_draft_id ? (
                          <span className="bj-small">Draft created in your Gmail</span>
                        ) : candidate.enriched_email ? (
                          <button
                            className="bj-primary"
                            onClick={() => void pushToGmail(candidate)}
                            disabled={pushingId === candidate.id}
                          >
                            {pushingId === candidate.id ? "Creating draft..." : "Create Gmail draft"}
                          </button>
                        ) : (
                          <span className="bj-muted bj-small">
                            Find this contact's work email to create a Gmail draft.
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
