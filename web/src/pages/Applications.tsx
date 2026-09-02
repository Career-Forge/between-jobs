import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import ApplicationsBoard from "../components/ApplicationsBoard";
import { apiFetch } from "../lib/api";
import { CROSS_NAV_ITEMS } from "../lib/applicationsBoard";
import { ALL_STATUSES, type Application, type ApplicationStatus } from "../lib/applicationsTypes";

// Applications (Sprint 2.6f) -- the first real surface over Sprint 2.6's
// schema (jobs/job_snapshots/applications/application_events/event_outbox/
// working_sets). Job creation here is the manual-paste lane only
// (Proposal §18): a client submits title/company/description[/url]
// directly. Real URL scraping (Firecrawl) is Sprint 3.0's job, layered on
// top of the same jobs_store later -- manual paste stays the permanent
// fallback for postings a scraper can't reach, not a placeholder.
//
// `Application`/`ApplicationStatus`/`ALL_STATUSES` now live in
// applicationsTypes.ts (Applications Kanban K2) so ApplicationsBoard.tsx
// and applicationsBoard.ts share the exact same shapes rather than
// reaching into this page. `status` is the real, server-enforced 7-value
// vocabulary as of K1 (a Postgres CHECK constraint plus
// change_application_stage's own copy of the same check) -- this page's
// own list already matched it exactly before K1 shipped, so the List view
// below is otherwise unchanged.
//
// Board vs. List defaults to List: a user who has never seen this toggle
// should see the exact page they saw before K2 shipped, not be dropped
// into a new view they didn't ask for.

export default function Applications() {
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // A Set, not a single id: the Board can have several cards in flight at
  // once (a drag on one card while another card's "Move to..." select is
  // still awaiting its response), and a single shared busy id would let
  // the earlier request's card render as no-longer-busy the moment a
  // second card's move starts -- re-enabling it for a concurrent, racing
  // stage change. See applications-kanban.md K2 for the concrete scenario.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [view, setView] = useState<"list" | "board">("list");

  const load = useCallback(async () => {
    try {
      const rows = await apiFetch<Application[]>("/applications");
      setApplications(rows);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load applications");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function changeStatus(applicationId: string, newStatus: ApplicationStatus) {
    setBusyIds((current) => new Set(current).add(applicationId));
    try {
      await apiFetch(`/applications/${applicationId}/stage`, {
        method: "POST",
        body: JSON.stringify({
          new_status: newStatus,
          idempotency_key: crypto.randomUUID(),
        }),
      });
      await load();
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to change status");
    } finally {
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(applicationId);
        return next;
      });
    }
  }

  return (
    <div>
      <h1>Applications</h1>
      <PasteJobForm onCreated={load} />
      {loadError && <div className="bj-error">{loadError}</div>}
      {applications !== null && applications.length === 0 && (
        <div className="bj-empty">
          <h2>Nothing tracked yet</h2>
          <p>Paste a job posting above to start tracking it.</p>
        </div>
      )}
      {applications !== null && applications.length > 0 && (
        <>
          <div className="bj-profile-toolbar">
            <div className="bj-view-toggle">
              <button
                className={view === "list" ? "bj-toggle-active" : ""}
                onClick={() => setView("list")}
              >
                List
              </button>
              <button
                className={view === "board" ? "bj-toggle-active" : ""}
                onClick={() => setView("board")}
              >
                Board
              </button>
            </div>
          </div>
          {view === "list" ? (
            <div className="bj-application-list">
              {applications.map((a) => (
                <ApplicationRow
                  key={a.id}
                  application={a}
                  busy={busyIds.has(a.id)}
                  onChangeStatus={(status) => void changeStatus(a.id, status)}
                />
              ))}
            </div>
          ) : (
            <ApplicationsBoard
              applications={applications}
              busyIds={busyIds}
              onChangeStatus={(applicationId, status) => void changeStatus(applicationId, status)}
            />
          )}
        </>
      )}
    </div>
  );
}

function ApplicationRow({
  application,
  busy,
  onChangeStatus,
}: {
  application: Application;
  busy: boolean;
  onChangeStatus: (status: ApplicationStatus) => void;
}) {
  const snapshot = application.snapshot;
  const navigate = useNavigate();
  return (
    <div className="bj-card bj-application-row">
      <div className="bj-card-header">
        <div>
          <div className="bj-application-title">{snapshot?.title ?? "Untitled"}</div>
          <div className="bj-muted bj-small">
            {snapshot?.company_name}
            {snapshot?.location_text ? ` -- ${snapshot.location_text}` : ""}
          </div>
        </div>
        <span className={statusBadgeClass(application.status)}>{application.status}</span>
      </div>
      <div className="bj-actions">
        <select
          value={application.status}
          disabled={busy}
          onChange={(e) => onChangeStatus(e.target.value as ApplicationStatus)}
        >
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {snapshot?.source_url && (
          <a href={snapshot.source_url} target="_blank" rel="noreferrer">
            View posting
          </a>
        )}
        <Link to={`/applications/${application.id}`}>Open workspace</Link>
        <select
          aria-label={`Actions for "${snapshot?.title ?? "Untitled"}"`}
          value=""
          onChange={(e) => {
            const hash = e.target.value;
            if (hash) navigate(`/applications/${application.id}#${hash}`);
          }}
        >
          <option value="">Actions...</option>
          {CROSS_NAV_ITEMS.map((item) => (
            <option key={item.hash} value={item.hash}>
              {item.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

function statusBadgeClass(status: ApplicationStatus): string {
  switch (status) {
    case "saved":
      return "bj-badge-gold";
    case "applied":
      return "bj-badge-cyan";
    case "screening":
    case "interviewing":
      return "bj-badge-violet";
    case "offer":
      return "bj-badge-emerald";
    case "rejected":
    case "withdrawn":
      return "bj-badge-danger";
    default:
      return "bj-badge-gold";
  }
}

function PasteJobForm({ onCreated }: { onCreated: () => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [descriptionText, setDescriptionText] = useState("");
  const [canonicalUrl, setCanonicalUrl] = useState("");
  const [locationText, setLocationText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/applications", {
        method: "POST",
        body: JSON.stringify({
          title,
          company_name: companyName,
          description_text: descriptionText,
          canonical_url: canonicalUrl.trim() || null,
          location_text: locationText.trim() || null,
        }),
      });
      setTitle("");
      setCompanyName("");
      setDescriptionText("");
      setCanonicalUrl("");
      setLocationText("");
      await onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = title.trim() !== "" && companyName.trim() !== "" && descriptionText.trim() !== "";

  return (
    <div className="bj-card">
      <h2>Track a job</h2>
      <p className="bj-muted">
        Paste a job posting -- title, company, and the description -- to start tracking it. No
        scraping yet: this is the manual lane, always available even when a URL isn't (an emailed
        posting, a screenshot, anything behind a login).
      </p>
      <label className="bj-field">
        <span>Job title *</span>
        <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="bj-field">
        <span>Company *</span>
        <input type="text" value={companyName} onChange={(e) => setCompanyName(e.target.value)} />
      </label>
      <label className="bj-field">
        <span>Location</span>
        <input type="text" value={locationText} onChange={(e) => setLocationText(e.target.value)} />
      </label>
      <label className="bj-field">
        <span>Posting URL</span>
        <input
          type="text"
          value={canonicalUrl}
          placeholder="https://..."
          onChange={(e) => setCanonicalUrl(e.target.value)}
        />
      </label>
      <label className="bj-field">
        <span>Description *</span>
        <textarea
          rows={6}
          value={descriptionText}
          onChange={(e) => setDescriptionText(e.target.value)}
        />
      </label>
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        <button className="bj-primary" onClick={() => void submit()} disabled={busy || !canSubmit}>
          {busy ? "Saving..." : "Start tracking"}
        </button>
      </div>
    </div>
  );
}
