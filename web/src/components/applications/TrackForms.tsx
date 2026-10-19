import { useState, type FormEvent } from "react";
import { apiFetch } from "../../lib/api";
import { SETTINGS_PATH } from "../../lib/settingsPaths";
import { failureOf, type Failure } from "../../lib/setupRequired";
import { FailureState, SetupRequiredState } from "../paper/StateViews";
import "../../styles/applications.css";

// The two ways to start tracking a job: fetch a posting by its URL, or paste the whole posting. Both
// create an application through the same requests the page has always made, and both leave the page
// to re-read its list (`onCreated`). They are drawn secondary: the page's one cyan fill is the
// next-step band's button, and when there is nothing tracked yet that button leads here.
//
// A failure stays on its own form. A 409 SETUP_REQUIRED from the URL form -- no Firecrawl key saved,
// and the URL is not in the registry with its description -- names what is missing with a link to
// where it is fixed, and says the paste form beside it needs no key.

// The id the band's button and the setup checklist scroll to: the paste form, which needs no key.
export const START_TRACKING_ID = "start-tracking";
export const START_TRACKING_HEADING_ID = "bj-app-paste-heading";

function FormProblem({ problem }: { problem: Failure | null }) {
  if (problem === null) return null;
  if (problem.kind === "setup") {
    const { notice } = problem;
    return (
      <SetupRequiredState
        message={`${notice.message} You can also paste the posting instead.`}
        missing={[{ label: notice.linkLabel ?? "Open Settings", to: notice.linkTo ?? SETTINGS_PATH }]}
      />
    );
  }
  return <FailureState message={problem.message} />;
}

export function UrlIngestForm({ onCreated }: { onCreated: () => Promise<void> }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Failure | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || url.trim() === "") return;
    setBusy(true);
    setProblem(null);
    try {
      await apiFetch("/applications/from-url", {
        method: "POST",
        body: JSON.stringify({ url: url.trim() }),
      });
      setUrl("");
      await onCreated();
    } catch (e) {
      // A URL that is not in the registry needs the person's own Firecrawl key to fetch.
      setProblem(failureOf(e, "Failed to fetch that posting"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="bj-app-track-card" onSubmit={(event) => void submit(event)}>
      <h3 className="bj-app-track-title">Fetch a posting by URL</h3>
      <p className="bj-app-track-note">
        Paste the posting's own address. If it is already in the job registry with its description, that text is used. Otherwise it is
        fetched with your Firecrawl key, which you add in Settings. LinkedIn and X addresses are never fetched -- paste those instead.
      </p>
      <label className="bj-field">
        <span>Posting URL *</span>
        <input type="text" value={url} placeholder="https://..." onChange={(e) => setUrl(e.target.value)} />
      </label>
      <FormProblem problem={problem} />
      <div className="bj-actions">
        <button type="submit" aria-disabled={busy || url.trim() === "" ? true : undefined}>
          {busy ? "Fetching..." : "Fetch and start tracking"}
        </button>
      </div>
    </form>
  );
}

export function PasteJobForm({ onCreated }: { onCreated: () => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [descriptionText, setDescriptionText] = useState("");
  const [canonicalUrl, setCanonicalUrl] = useState("");
  const [locationText, setLocationText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim() !== "" && companyName.trim() !== "" && descriptionText.trim() !== "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !canSubmit) return;
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

  return (
    <form id={START_TRACKING_ID} className="bj-app-track-card" onSubmit={(event) => void submit(event)}>
      <h3 className="bj-app-track-title" id={START_TRACKING_HEADING_ID} tabIndex={-1}>
        Paste the full posting
      </h3>
      <p className="bj-app-track-note">Always available, and needs no key: use it for an emailed posting, or anything behind a login.</p>
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
        <input type="text" value={canonicalUrl} placeholder="https://..." onChange={(e) => setCanonicalUrl(e.target.value)} />
      </label>
      <label className="bj-field">
        <span>Description *</span>
        <textarea rows={6} value={descriptionText} onChange={(e) => setDescriptionText(e.target.value)} />
      </label>
      {error !== null && <FailureState message={error} />}
      <div className="bj-actions">
        <button type="submit" aria-disabled={busy || !canSubmit ? true : undefined}>
          {busy ? "Saving..." : "Start tracking"}
        </button>
      </div>
    </form>
  );
}
