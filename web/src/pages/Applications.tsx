import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";

// Applications (Sprint 2.6f) -- the first real surface over Sprint 2.6's
// schema (jobs/job_snapshots/applications/application_events/event_outbox/
// working_sets). Job creation here is the manual-paste lane only
// (Proposal §18): a client submits title/company/description[/url]
// directly. Real URL scraping (Firecrawl) is Sprint 3.0's job, layered on
// top of the same jobs_store later -- manual paste stays the permanent
// fallback for postings a scraper can't reach, not a placeholder.
//
// `status` is unconstrained text at the DB layer (applications_store.py's
// own choice, matching Proposal's DDL). STATUS_OPTIONS below is this
// page's own small vocabulary for the dropdown -- not enforced anywhere
// server-side.

interface JobSnapshot {
  id: string;
  title: string;
  company_name: string;
  location_text: string | null;
  source_url: string;
}

interface Application {
  id: string;
  status: string;
  source_channel: string;
  created_at: string;
  updated_at: string;
  snapshot: JobSnapshot | null;
}

const STATUS_OPTIONS = [
  "saved",
  "applied",
  "screening",
  "interviewing",
  "offer",
  "rejected",
  "withdrawn",
];

export default function Applications() {
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

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

  async function changeStatus(applicationId: string, newStatus: string) {
    setBusyId(applicationId);
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
      setBusyId(null);
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
        <div className="bj-application-list">
          {applications.map((a) => (
            <ApplicationRow
              key={a.id}
              application={a}
              busy={busyId === a.id}
              onChangeStatus={(status) => void changeStatus(a.id, status)}
            />
          ))}
        </div>
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
  onChangeStatus: (status: string) => void;
}) {
  const snapshot = application.snapshot;
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
          onChange={(e) => onChangeStatus(e.target.value)}
        >
          {STATUS_OPTIONS.map((s) => (
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
      </div>
    </div>
  );
}

function statusBadgeClass(status: string): string {
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
