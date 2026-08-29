import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { HeaderComposer } from "../components/HeaderComposer";
import { SectionedProfile } from "../components/SectionedProfile";
import { SectionOrderEditor } from "../components/SectionOrderEditor";
import { ShapeSettingsPanel } from "../components/ShapeSettingsPanel";
import { ApiError, apiFetch } from "../lib/api";
import type { CanonicalProfile } from "../lib/profileTypes";
import { CONVERSION_PROMPT, RESUME_TEMPLATE } from "../lib/template";
import { useProfileEditor } from "../lib/useProfileEditor";

// Profile onboarding (Sprint 3.1c) -- the web twin of the Telegram
// setup-resume flow, wired to the same /profile endpoints and the same
// deterministic server-side importer. The flow mirrors the reference
// implementation's master-resume UX (template download + conversion
// prompt + review-before-activate), with the preview-then-confirm step
// the canonical contract requires: nothing becomes your active profile
// until you explicitly activate it.
//
// Sprint 3.1d replaced the stats-summary body with the real sectioned
// view (SectionedProfile). Sprint 3.1e adds per-section editing --
// useProfileEditor is instantiated once here (it needs the current
// profile + a way to reload it) and threaded down to SectionedProfile,
// which owns which entry is being edited and renders the modal.

interface ProfileVersion {
  id: string;
  canonical_json: CanonicalProfile;
  activated_at: string | null;
  created_at?: string;
  stats?: Record<string, number>;
  warnings?: string[];
  version_count?: number;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "none" }
  | { kind: "active"; version: ProfileVersion }
  | { kind: "pending"; version: ProfileVersion; hadActive: boolean };

export default function Profile() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [importError, setImportError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadCurrent = useCallback(async () => {
    try {
      const version = await apiFetch<ProfileVersion>("/profile/current");
      setState({ kind: "active", version });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setState({ kind: "none" });
      } else {
        setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
      }
    }
  }, []);

  useEffect(() => {
    void loadCurrent();
  }, [loadCurrent]);

  async function importJson(rawText: string) {
    setBusy(true);
    setImportError(null);
    const hadActive = state.kind === "active";
    try {
      const version = await apiFetch<ProfileVersion>("/profile/versions", {
        method: "POST",
        body: JSON.stringify({ raw_text: rawText }),
      });
      setState({ kind: "pending", version, hadActive });
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  async function activate(versionId: string) {
    setBusy(true);
    try {
      await apiFetch(`/profile/versions/${versionId}/activate`, { method: "POST" });
      await loadCurrent();
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Activation failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancel(versionId: string) {
    setBusy(true);
    try {
      await apiFetch(`/profile/versions/${versionId}`, { method: "DELETE" });
    } catch {
      // Already gone or already activated -- either way the pending view
      // is stale; fall through to reload the truth.
    }
    setImportError(null);
    await loadCurrent();
    setBusy(false);
  }

  if (state.kind === "loading") {
    return <PageFrame />;
  }

  if (state.kind === "error") {
    return (
      <PageFrame>
        <div className="bj-error">{state.message}</div>
      </PageFrame>
    );
  }

  if (state.kind === "pending") {
    return (
      <PageFrame>
        <PendingPreview
          version={state.version}
          busy={busy}
          error={importError}
          onActivate={() => void activate(state.version.id)}
          onCancel={() => void cancel(state.version.id)}
        />
      </PageFrame>
    );
  }

  return (
    <PageFrame>
      {state.kind === "active" && (
        <ActiveProfile version={state.version} onEdited={loadCurrent} />
      )}
      <ImportSection
        replacing={state.kind === "active"}
        busy={busy}
        error={importError}
        onImport={(text) => void importJson(text)}
      />
    </PageFrame>
  );
}

function PageFrame({ children }: { children?: React.ReactNode }) {
  const navigate = useNavigate();
  return (
    <div>
      <div className="bj-card-header">
        <h1>Profile</h1>
        <button onClick={() => navigate("/profile/integrations")}>Integrations</button>
      </div>
      {children}
    </div>
  );
}

function ActiveProfile({
  version,
  onEdited,
}: {
  version: ProfileVersion;
  onEdited: () => Promise<void>;
}) {
  const [view, setView] = useState<"sections" | "json">("sections");
  const [copied, setCopied] = useState(false);
  const editor = useProfileEditor(version.canonical_json, onEdited);
  const personal = version.canonical_json.personal;
  const scholar = personal.links?.scholar;
  const jsonText = JSON.stringify(version.canonical_json, null, 2);

  function exportJson() {
    downloadJson(version.canonical_json, "between-jobs-profile.json");
  }

  async function copyJson() {
    await navigator.clipboard.writeText(jsonText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div>
      <div className="bj-card bj-profile-header">
        <div className="bj-card-header">
          <div>
            <h1 className="bj-profile-name">{personal.name ?? "Unnamed"}</h1>
            {personal.headline && <div className="bj-profile-headline">{personal.headline}</div>}
          </div>
          <span className="bj-badge-emerald">Active</span>
        </div>
        <HeaderComposer />
        {scholar && (
          // forge-engines' header fields (Sprint 3.2b) don't include Google
          // Scholar yet -- a real, pre-existing gap in the engine's header
          // model, not something to paper over. Shown as a plain,
          // non-reorderable chip until the composer supports it.
          <div className="bj-contact-chips">
            <a
              className="bj-contact-chip"
              href={withProtocol(scholar)}
              target="_blank"
              rel="noreferrer"
            >
              {scholar}
            </a>
          </div>
        )}
        <div className="bj-profile-toolbar">
          <div className="bj-view-toggle">
            <button
              className={view === "sections" ? "bj-toggle-active" : ""}
              onClick={() => setView("sections")}
            >
              Sections
            </button>
            <button className={view === "json" ? "bj-toggle-active" : ""} onClick={() => setView("json")}>
              Raw JSON
            </button>
          </div>
          <div className="bj-actions">
            <button onClick={exportJson}>Export JSON</button>
            {view === "json" && (
              <button onClick={() => void copyJson()}>{copied ? "Copied ✓" : "Copy"}</button>
            )}
          </div>
        </div>
        <div className="bj-muted bj-small">
          Activated {version.activated_at ? new Date(version.activated_at).toLocaleString() : ""}
        </div>
      </div>

      <div className="bj-card">
        <h2>Resume structure</h2>
        <p className="bj-muted bj-small">
          Drag to reorder, or hide a section -- this controls the resume that gets generated, not
          your saved profile data.
        </p>
        <SectionOrderEditor profile={version.canonical_json} />
      </div>

      <div className="bj-card">
        <h2>Resume settings</h2>
        <p className="bj-muted bj-small">
          Defaults for every application -- a specific application can override any of these.
        </p>
        <ShapeSettingsPanel />
      </div>

      {view === "sections" ? (
        <SectionedProfile
          profile={version.canonical_json}
          versionCount={version.version_count ?? 1}
          onSave={editor.save}
          saving={editor.saving}
          error={editor.error}
          clearError={editor.clearError}
        />
      ) : (
        <pre className="bj-json-view">{jsonText}</pre>
      )}
    </div>
  );
}

function withProtocol(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}

function PendingPreview({
  version,
  busy,
  error,
  onActivate,
  onCancel,
}: {
  version: ProfileVersion;
  busy: boolean;
  error: string | null;
  onActivate: () => void;
  onCancel: () => void;
}) {
  const personal = version.canonical_json.personal;
  const stats = version.stats ?? {};
  const warnings = version.warnings ?? [];

  return (
    <div className="bj-card">
      <div className="bj-card-header">
        <h2>Review before saving</h2>
        <span className="bj-badge-gold">Pending</span>
      </div>
      <p className="bj-muted">
        Parsed deterministically -- no AI touched this. Check it, then activate.
      </p>
      <div>
        <strong>{personal.name}</strong>
        {personal.headline && <span className="bj-muted"> -- {personal.headline}</span>}
      </div>
      <div className="bj-stat-row">
        <Stat label="Experience" value={stats.experience ?? 0} />
        <Stat label="Projects" value={stats.projects ?? 0} />
        <Stat label="Education" value={stats.education ?? 0} />
        <Stat label="Skills" value={stats.skills ?? 0} />
      </div>
      {warnings.length > 0 && (
        <div className="bj-warnings">
          {warnings.map((w) => (
            <div key={w}>⚠ {w}</div>
          ))}
        </div>
      )}
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        <button className="bj-primary" onClick={onActivate} disabled={busy}>
          Looks good -- activate
        </button>
        <button onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function ImportSection({
  replacing,
  busy,
  error,
  onImport,
}: {
  replacing: boolean;
  busy: boolean;
  error: string | null;
  onImport: (rawText: string) => void;
}) {
  const [pasted, setPasted] = useState("");
  const [promptCopied, setPromptCopied] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function onFileChosen(files: FileList | null) {
    const file = files?.[0];
    if (!file) {
      return;
    }
    onImport(await file.text());
    if (fileInput.current) {
      fileInput.current.value = "";
    }
  }

  async function copyPrompt() {
    await navigator.clipboard.writeText(CONVERSION_PROMPT);
    setPromptCopied(true);
    setTimeout(() => setPromptCopied(false), 2000);
  }

  return (
    <div className="bj-card">
      <h2>{replacing ? "Import a new version" : "Set up your profile"}</h2>
      <p className="bj-muted">
        Your resume lives here as structured JSON -- deterministic, private, and reviewed by
        you before anything is saved. Fill the template (an AI assistant can do the
        conversion; only the fixed template ever gets imported), then paste or upload it.
      </p>
      <div className="bj-actions">
        <button onClick={() => downloadJson(RESUME_TEMPLATE, "between-jobs-template.json")}>
          Download blank template
        </button>
        <button onClick={() => void copyPrompt()}>
          {promptCopied ? "Copied ✓" : "Copy conversion prompt"}
        </button>
        <button onClick={() => fileInput.current?.click()} disabled={busy}>
          Upload .json file
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".json,application/json"
          style={{ display: "none" }}
          onChange={(e) => void onFileChosen(e.target.files)}
        />
      </div>
      <textarea
        rows={8}
        placeholder='Paste your filled template here ({"personal": ...)'
        value={pasted}
        onChange={(e) => setPasted(e.target.value)}
      />
      {error && <div className="bj-error">{error}</div>}
      <div className="bj-actions">
        <button
          className="bj-primary"
          onClick={() => onImport(pasted)}
          disabled={busy || pasted.trim() === ""}
        >
          {busy ? "Importing..." : "Import"}
        </button>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bj-stat">
      <div className="bj-stat-value">{value}</div>
      <div className="bj-stat-label">{label}</div>
    </div>
  );
}

function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
