import { useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "../lib/api";
import { copyToClipboard } from "../lib/clipboard";
import { gmailAccess, gmailState, gmailUnavailableMessage, learnFromGmailRefusal } from "../lib/gmailAccess";
import { subjectAndBody } from "../lib/mailto";
import { contactConfidence, countWords, runDate, safeSourceUrl } from "../lib/research";
import { contactsTrail, draftTrail, failedTrail } from "../lib/researchTrails";
import { failureOf, type Failure } from "../lib/setupRequired";
import { SETTINGS_PATH } from "../lib/settingsPaths";
import { refreshCapabilities, useCapabilities } from "../lib/useCapabilities";
import { useGmailConnection } from "../lib/useGmailConnection";
import { useNow } from "../lib/useNow";
import { useResearchRun } from "../lib/useResearchRun";
import { GmailDraftActions } from "./GmailDraftActions";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState, PartialState } from "./paper/StateViews";
import { TrailLine } from "./paper/TrailLine";
import { EmptyBlock } from "./research/EmptyBlock";
import { Chip, EvidenceItem, EvidenceList, EvidenceNote } from "./research/Evidence";
import { RunButton, RunProblem, RunWarnings } from "./research/RunParts";
import { SetupRequiredNotice } from "./SetupRequiredNotice";
import "../styles/workspace.css";
import "../styles/research.css";

// Contacts: people the web publicly names for this company, each linked to the source that names them
// (the section's one evidence pattern, components/research/Evidence.tsx), and the opt-in steps from there.
//
//   a run             searches with the person's own You.com or Firecrawl key and asks their model to pick
//                     out names; code keeps a name only when the source's own title or snippet contains it,
//                     and ranks the people (the kind of role, how many sources name them, hiring language).
//                     No email address or phone number comes with a run.
//   Find work email   one person at a time, on the person's own Apollo or Hunter credit, behind a
//                     confirm that says so
//   Find LinkedIn     one person at a time, on the person's own Exa credit, behind a confirm too
//   Draft outreach    the person's model writes four messages from that contact's sources
//
// OUTREACH STAYS A DRAFT. Nothing here sends anything. A draft is shown with its four messages and a
// Copy for each; under it, GmailDraftActions offers the ways out that match the person's own Gmail state
// (an optional, invite-only connection: a draft saved in their Gmail, or their mail app, or the clipboard).
// The copy under every draft says plainly that nothing is sent.
//
// A failure of one person's lookup or draft sits on that person's row, not at the top of the panel, and
// the server declining to draft because it had nothing to ground a draft in (INSUFFICIENT_EVIDENCE) is a
// gold note, not an error. A failed run keeps the people already shown (lib/useResearchRun.ts).

type Confidence = string;

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
  // Set on every enrichment attempt, success or failure -- distinguishes "never tried" (null) from
  // "tried, found nothing" (set, but enriched_email is still null), so the UI does not invite an
  // unlimited number of repeat paid lookups with no indication a real provider credit was already spent.
  enriched_at: string | null;
  // A distinct opt-in action from email enrichment (never an email). The confidence is the lookup's own
  // word ("strong", "inferred", "unsupported"), never a verification.
  discovered_linkedin_url: string | null;
  linkedin_discovery_confidence: string | null;
  linkedin_discovery_provider: string | null;
  // Same "never tried" vs "tried, found nothing" distinction as enriched_at above.
  linkedin_discovered_at: string | null;
  // The hook the outreach writer would pick for this person -- null only when there is no evidence to
  // hook from.
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
  created_at: string;
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

type Action = "email" | "linkedin" | "draft" | "gmail";

// What went wrong (or was said) about one person's lookup or draft: a setup notice, an error with a
// Retry, or a plain note.
type Issue = { action: Action; problem: Failure | { kind: "note"; message: string } };

// How long "Couldn't copy" stays up before the button reads as before.
const COPY_FAILED_NOTICE_MS = 6000;

const PERSONA_LABELS: Record<string, string> = {
  hiring_lead: "Hiring lead",
  recruiter: "Recruiter",
  manager: "Engineering manager",
  senior_leader: "Senior leader",
  senior_ic: "Senior IC",
};

// The field, its label, and what a Copy button for it is called after "Copy" (the label with the capitals a
// name keeps: "LinkedIn message").
const DRAFT_FIELDS = [
  ["subject", "Subject", "subject"],
  ["email_body", "Email", "email"],
  ["linkedin_message", "LinkedIn message", "LinkedIn message"],
  ["follow_up_message", "Follow-up", "follow-up"],
] as const;

function DraftTrail({ draft, now }: { draft: OutreachDraft; now: Date }) {
  const parts = draftTrail(draft, now);
  return <TrailLine trail={parts.trail} receipt={parts.receipt} receiptTitle={parts.receiptTitle} />;
}

export function ContactFinderPanel({ applicationId, now }: { applicationId: string; now?: Date }) {
  const clock = useNow(now);
  const { read, running, problem, load, run, update, runButton, focusRunButton } = useResearchRun<ContactsResponse>(`/applications/${applicationId}/contacts`, {
    read: "Failed to load",
    run: "Failed to generate",
  });
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [issues, setIssues] = useState<Readonly<Record<string, Issue>>>({});
  const [drafts, setDrafts] = useState<Record<string, OutreachDraft>>({});
  const [copiedField, setCopiedField] = useState<string | null>(null);
  // The field whose copy the browser refused, so its button can say so instead of seeming dead.
  const [copyFailedField, setCopyFailedField] = useState<string | null>(null);
  // What the last lookup or draft came to, for the polite region that exists before it is filled: a result
  // that simply appears in the page is not reliably announced. Cleared when the next action starts.
  const [outcome, setOutcome] = useState("");
  // Whether Gmail is open to this person and what state their connection is in decide what sits
  // under a draft (GmailDraftActions). Their connection is read only once Gmail is known to be open
  // to them: nothing is asked when it is not for them, nor while that is not known.
  const access = gmailAccess(useCapabilities());
  const { connection, markReconnectNeeded } = useGmailConnection(access === "available");
  const gmail = gmailState(access, connection);

  // What a Copy came to, from the state that already resets itself: the same words for the status region.
  const copyKeyHeld = copiedField ?? copyFailedField;
  const copiedNoun =
    copyKeyHeld === null ? null : copyKeyHeld.endsWith("-all") ? "subject and email" : (DRAFT_FIELDS.find(([field]) => copyKeyHeld.endsWith(`-${field}`))?.[2] ?? null);
  const copyWords = copiedNoun === null ? "" : copiedField !== null ? `Copied the ${copiedNoun}` : `Couldn't copy the ${copiedNoun}`;

  const headingId = (candidate: Candidate) => `contact-${candidate.id}`;
  const focusHeading = (candidate: Candidate) => () => document.getElementById(headingId(candidate));

  // A button that held focus can go when its action ends (a found email replaces "Find work email" and its
  // Retry). Focus would drop to the page body, so once the page has settled it goes to the person's name,
  // which stays.
  const refocusRow = useRef<string | null>(null);
  useEffect(() => {
    const id = refocusRow.current;
    if (id === null) return;
    refocusRow.current = null;
    if (document.activeElement === null || document.activeElement === document.body) document.getElementById(`contact-${id}`)?.focus();
  });

  function keepFocusInRow(candidate: Candidate): () => void {
    const row = document.getElementById(headingId(candidate))?.closest("li") ?? null;
    const held = row !== null && row.contains(document.activeElement);
    return () => {
      if (held) refocusRow.current = candidate.id;
    };
  }

  const key = (candidate: Candidate, action: Action) => `${candidate.id}:${action}`;

  function start(candidate: Candidate, action: Action): boolean {
    if (busy.has(key(candidate, action))) return false;
    setOutcome("");
    setBusy((previous) => new Set(previous).add(key(candidate, action)));
    setIssues((previous) => {
      const { [candidate.id]: _cleared, ...rest } = previous;
      return rest;
    });
    return true;
  }

  function finish(candidate: Candidate, action: Action) {
    setBusy((previous) => {
      const next = new Set(previous);
      next.delete(key(candidate, action));
      return next;
    });
  }

  function fail(candidate: Candidate, action: Action, problem: Issue["problem"]) {
    setIssues((previous) => ({ ...previous, [candidate.id]: { action, problem } }));
  }

  // /enrich and /find-linkedin return only the raw contact_candidates row they updated: no `evidence` (a
  // separate table) and no `approach_hint` (made only by the list endpoint). A full replace would drop
  // those and crash the next render's evidence list, so the reply is merged onto the existing person.
  function merge(updated: Candidate) {
    update((data) => ({ ...data, candidates: data.candidates.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)) }));
  }

  async function enrich(candidate: Candidate) {
    // A press on a button that is already looking up is ignored before the confirm: it would ask the person
    // to approve a paid lookup that is not going to happen.
    if (busy.has(key(candidate, "email"))) return;
    const confirmed = window.confirm(
      `Look up a confirmed work email for ${candidate.person_name}? This uses your own Apollo ` + "and/or Hunter credit for one lookup -- never a bulk search, never personal contact info.",
    );
    if (!confirmed || !start(candidate, "email")) return;
    const settled = keepFocusInRow(candidate);
    try {
      const updated = await apiFetch<Candidate>(`/applications/${applicationId}/contacts/${candidate.id}/enrich`, { method: "POST" });
      merge(updated);
      setOutcome(updated.enriched_email ? `Work email found for ${candidate.person_name}` : `No confirmed email found for ${candidate.person_name}`);
    } catch (e) {
      fail(candidate, "email", failureOf(e, "Enrichment failed"));
    } finally {
      finish(candidate, "email");
      settled();
    }
  }

  async function findLinkedIn(candidate: Candidate) {
    if (busy.has(key(candidate, "linkedin"))) return;
    const confirmed = window.confirm(`Look up a LinkedIn profile for ${candidate.person_name} via Exa? This uses your own Exa ` + "credit for one lookup.");
    if (!confirmed || !start(candidate, "linkedin")) return;
    const settled = keepFocusInRow(candidate);
    try {
      const updated = await apiFetch<Candidate>(`/applications/${applicationId}/contacts/${candidate.id}/find-linkedin`, { method: "POST" });
      merge(updated);
      setOutcome(updated.discovered_linkedin_url ? `LinkedIn profile found for ${candidate.person_name}` : `No LinkedIn profile found for ${candidate.person_name}`);
    } catch (e) {
      fail(candidate, "linkedin", failureOf(e, "LinkedIn lookup failed"));
    } finally {
      finish(candidate, "linkedin");
      settled();
    }
  }

  async function draftOutreach(candidate: Candidate) {
    if (!start(candidate, "draft")) return;
    try {
      const result = await apiFetch<{ draft: OutreachDraft }>(`/applications/${applicationId}/contacts/${candidate.id}/draft-outreach`, { method: "POST" });
      setDrafts((previous) => ({ ...previous, [candidate.id]: result.draft }));
      setOutcome(`Outreach draft ready for ${candidate.person_name}. Nothing is sent.`);
    } catch (e) {
      // Nothing to ground a draft in is the server's honest answer, not a failure: its reason, as a note.
      if (e instanceof ApiError && e.code === "INSUFFICIENT_EVIDENCE") {
        fail(candidate, "draft", { kind: "note", message: e.message });
        setOutcome(e.message);
      }
      else fail(candidate, "draft", failureOf(e, "Drafting failed"));
    } finally {
      finish(candidate, "draft");
    }
  }

  async function copyText(field: string, text: string) {
    setCopiedField(null);
    setCopyFailedField(null);
    setOutcome("");
    if (await copyToClipboard(text)) {
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 2000);
    } else {
      setCopyFailedField(field);
      setTimeout(() => setCopyFailedField(null), COPY_FAILED_NOTICE_MS);
    }
  }

  async function pushToGmail(candidate: Candidate) {
    if (!start(candidate, "gmail")) return;
    try {
      const updated = await apiFetch<OutreachDraft>(`/applications/${applicationId}/contacts/${candidate.id}/push-to-gmail`, { method: "POST" });
      setDrafts((previous) => ({ ...previous, [candidate.id]: updated }));
      setOutcome(`Gmail draft saved for ${candidate.person_name}`);
    } catch (e) {
      // The server says what this page only believed: Google stopped accepting the connection, or
      // Gmail is not open to this person. Either way the draft can still be copied or opened in a
      // mail app, so show that now and not after a reload. This comes first and runs for every
      // failure: the reconnect answer is also a notice, which the lines below show.
      learnFromGmailRefusal(e, { reconnectNeeded: markReconnectNeeded, unavailable: refreshCapabilities });
      fail(candidate, "gmail", failureOf(e, "Couldn't create that Gmail draft."));
    } finally {
      finish(candidate, "gmail");
    }
  }

  function retry(candidate: Candidate, action: Action) {
    if (action === "email") void enrich(candidate);
    else if (action === "linkedin") void findLinkedIn(candidate);
    else if (action === "draft") void draftOutreach(candidate);
    else void pushToGmail(candidate);
  }

  const ready = read.kind === "ready" ? read.data : null;
  const data = ready !== null && ready.run !== null ? { run: ready.run, candidates: ready.candidates } : null;
  const researched = data === null ? null : runDate(data.run, clock);
  const meta = data === null ? [] : [researched === null ? null : `Researched ${researched}`, countWords(data.candidates.length, "person", "people")].filter((part): part is string => part !== null);

  const trail =
    data !== null
      ? contactsTrail(data.run, data.candidates.length, clock)
      : read.kind === "failed"
        ? failedTrail("Contacts couldn't be read")
        : problem?.kind === "error"
          ? failedTrail("That contact search didn't finish")
          : null;

  return (
    <div className="bj-rs">
      <p className="bj-ws-intro">
        People the web publicly names for this company, each linked to the source that names them. A run searches with your You.com or Firecrawl key and asks your model to pick out names, so it uses
        your own keys each time. No email address or phone number comes with it: looking one up is a separate step you choose, for one person at a time.
      </p>
      <RunButton
        label={data !== null ? "Refresh" : "Find contacts"}
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

      <div role="status" className="bj-visually-hidden">
        {outcome !== "" ? outcome : copyWords}
      </div>

      {read.kind === "loading" && <LoadingState rows={3} rowHeight={112} label="Loading contacts..." />}
      {read.kind === "failed" && <FailureState message={read.message} onRetry={() => void load()} restoreFocusTo={focusRunButton} />}
      <RunProblem problem={problem} onRetry={() => void run()} restoreFocusTo={focusRunButton} />

      {ready !== null && data === null && (
        <EmptyBlock heading="No contacts found yet" link={{ to: SETTINGS_PATH, label: "Keys are added in Settings" }}>
          <p>
            A run finds people the web publicly names for this company: a hiring lead, a recruiter, an engineering manager, a senior leader or a senior engineer, each with the source that names them and a
            way in to start the conversation. It gives no email address or phone number.
          </p>
          <p>It needs a model key and a You.com or Firecrawl key. Its hiring-post searches also need a Firecrawl key, and are skipped without one.</p>
        </EmptyBlock>
      )}

      {data !== null && (
        <>
          <RunWarnings run={data.run} />
          {data.candidates.length === 0 ? (
            <p className="bj-muted bj-small">No named people turned up in that search pass. Refresh to try again.</p>
          ) : (
            <ul className="bj-rs-list" aria-label="People named for this company">
              {data.candidates.map((candidate) => {
                const draft = drafts[candidate.id];
                const issue = issues[candidate.id];
                const title = [candidate.claimed_title, candidate.claimed_team].filter((part): part is string => typeof part === "string" && part !== "");
                const linkedin = safeSourceUrl(candidate.discovered_linkedin_url);
                const lookingUpEmail = busy.has(key(candidate, "email"));
                const lookingUpLinkedIn = busy.has(key(candidate, "linkedin"));
                return (
                  <li key={candidate.id} className="bj-rs-item">
                    <h3 id={headingId(candidate)} tabIndex={-1} className="bj-rs-title">
                      {candidate.person_name}
                    </h3>
                    {title.length > 0 && <p className="bj-rs-sub">{title.join(" · ")}</p>}
                    <p className="bj-rs-chips">
                      <Chip tone="muted">{PERSONA_LABELS[candidate.persona] ?? candidate.persona}</Chip>{" "}
                      <span className="bj-rs-priority">Priority {Math.round(candidate.priority_score)} / 100</span>
                    </p>
                    <p className="bj-rs-text">{candidate.relevance_reason}</p>
                    {candidate.approach_hint && (
                      <p className="bj-rs-text">
                        <strong>Approach:</strong> {candidate.approach_hint}
                      </p>
                    )}
                    <EvidenceList label={`Sources naming ${candidate.person_name}`}>
                      {(candidate.evidence ?? []).map((evidence) => (
                        <EvidenceItem key={evidence.id} url={evidence.source_url} title={evidence.source_title} confidence={contactConfidence(evidence.confidence)} />
                      ))}
                    </EvidenceList>

                    <div className="bj-rs-facts">
                      {candidate.enriched_email ? (
                        <p className="bj-rs-fact">
                          <span className="bj-rs-fact-label">Work email</span> {candidate.enriched_email}
                          {candidate.enriched_email_status ? ` (${candidate.enriched_email_status})` : ""}
                          {candidate.enrichment_provider ? ` via ${candidate.enrichment_provider}` : ""}
                        </p>
                      ) : candidate.enriched_at ? (
                        <p className="bj-rs-fact bj-muted">
                          No confirmed email found.{" "}
                          <button type="button" className="bj-link-button" aria-disabled={lookingUpEmail ? true : undefined} onClick={() => void enrich(candidate)}>
                            {lookingUpEmail ? "Looking up..." : "Retry"}
                          </button>
                        </p>
                      ) : null}
                      {linkedin !== null ? (
                        <p className="bj-rs-fact">
                          <a href={linkedin} target="_blank" rel="noopener noreferrer">
                            LinkedIn profile<span className="bj-visually-hidden"> for {candidate.person_name} (opens in a new tab)</span>
                          </a>
                          {candidate.linkedin_discovery_confidence ? ` · ${candidate.linkedin_discovery_confidence} match` : ""}
                          {candidate.linkedin_discovery_provider ? ` via ${candidate.linkedin_discovery_provider}` : ""}
                          {candidate.linkedin_discovery_confidence !== "strong" ? ". A name search can surface someone else: check it is the right person." : ""}
                        </p>
                      ) : candidate.linkedin_discovered_at ? (
                        <p className="bj-rs-fact bj-muted">
                          No LinkedIn profile found.{" "}
                          <button type="button" className="bj-link-button" aria-disabled={lookingUpLinkedIn ? true : undefined} onClick={() => void findLinkedIn(candidate)}>
                            {lookingUpLinkedIn ? "Looking up..." : "Retry"}
                          </button>
                        </p>
                      ) : null}
                    </div>

                    <div className="bj-actions">
                      {!candidate.enriched_email && !candidate.enriched_at && (
                        <button type="button" aria-disabled={lookingUpEmail ? true : undefined} onClick={() => void enrich(candidate)}>
                          {lookingUpEmail ? "Looking up..." : "Find work email"}
                        </button>
                      )}
                      {!candidate.discovered_linkedin_url && !candidate.linkedin_discovered_at && (
                        <button type="button" aria-disabled={lookingUpLinkedIn ? true : undefined} onClick={() => void findLinkedIn(candidate)}>
                          {lookingUpLinkedIn ? "Looking up..." : "Find LinkedIn"}
                        </button>
                      )}
                      <button type="button" aria-disabled={busy.has(key(candidate, "draft")) ? true : undefined} onClick={() => void draftOutreach(candidate)}>
                        {busy.has(key(candidate, "draft")) ? "Drafting..." : draft ? "Redraft outreach" : "Draft outreach"}
                      </button>
                    </div>

                    {issue !== undefined && (
                      <div className="bj-rs-issue">
                        {issue.problem.kind === "setup" ? (
                          <SetupRequiredNotice notice={issue.problem.notice} />
                        ) : issue.problem.kind === "note" ? (
                          <EvidenceNote announce={false}>{issue.problem.message}</EvidenceNote>
                        ) : (
                          <FailureState message={issue.problem.message} onRetry={() => retry(candidate, issue.action)} restoreFocusTo={focusHeading(candidate)} />
                        )}
                      </div>
                    )}

                    {draft && (
                      <div className="bj-rs-draft" role="group" aria-label={`Outreach draft for ${candidate.person_name}`}>
                        <p className="bj-rs-draft-state">Draft -- not sent</p>
                        <DraftTrail draft={draft} now={clock} />
                        {draft.rubric_warnings.length > 0 && <PartialState lead="A code check flagged this draft:" notices={draft.rubric_warnings} />}
                        {DRAFT_FIELDS.map(([field, label, noun]) => {
                          const text = draft[field];
                          const copyKey = `${candidate.id}-${field}`;
                          return (
                            <div key={field} className="bj-rs-field">
                              <h4 className="bj-rs-label">{label}</h4>
                              <p className="bj-rs-well">{text}</p>
                              {/* The visible word is the start of the name, so the name carries what is copied. */}
                              <button type="button" className="bj-link-button" onClick={() => void copyText(copyKey, text)}>
                                {copiedField === copyKey ? "Copied!" : copyFailedField === copyKey ? "Couldn't copy" : "Copy"}
                                <span className="bj-visually-hidden"> {noun}</span>
                              </button>
                            </div>
                          );
                        })}
                        <p className="bj-ws-caption">Nothing is sent from here. Copy whichever message you want and send it yourself.</p>
                        <GmailDraftActions
                          state={gmail}
                          unavailableMessage={gmailUnavailableMessage(access)}
                          alreadyPushed={Boolean(draft.gmail_draft_id)}
                          recipient={candidate.enriched_email}
                          subject={draft.subject}
                          body={draft.email_body}
                          pushing={busy.has(key(candidate, "gmail"))}
                          copied={copiedField === `${candidate.id}-all`}
                          copyFailed={copyFailedField === `${candidate.id}-all`}
                          onPush={() => void pushToGmail(candidate)}
                          onCopyAll={() => void copyText(`${candidate.id}-all`, subjectAndBody({ subject: draft.subject, body: draft.email_body }))}
                        />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}

      {trail !== null && <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />}
    </div>
  );
}
