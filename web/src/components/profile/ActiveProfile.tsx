import { useState } from "react";
import { activeProfileTrail } from "../../lib/profileTrails";
import { downloadJson, withProtocol } from "../../lib/profileDownload";
import type { ProfileVersion } from "../../lib/profileTypes";
import { useProfileEditor } from "../../lib/useProfileEditor";
import { HeaderComposer } from "../HeaderComposer";
import { DocumentBlock } from "../paper/DocumentLayout";
import { SectionChapter } from "../paper/SectionChapter";
import { PersonalDetailsCard } from "../PersonalDetailsCard";
import { SectionedProfile } from "../SectionedProfile";
import { SectionOrderEditor } from "../SectionOrderEditor";
import { ShapeSettingsPanel } from "../ShapeSettingsPanel";
import { ViewToggle } from "../ViewToggle";
import "../../styles/profile.css";

// The person's active profile as a document: who it is, their personal details, the resume layout
// defaults (header, section order, settings) and the sections themselves, each section editable.
// It draws blocks and chapters for the page's DocumentLayout and must be a child of one (its blocks
// put their trail lines in the layout's margin, or under themselves at narrower widths).
//
// One `useProfileEditor` is made here, since it needs the current profile and a way to reload it, and
// is shared by Personal details and the sections: both save through the same versioned-import
// pipeline (every save is a new version), and `saving` is genuinely shared. Their failure messages are
// not: each card keeps its own (see PersonalDetailsCard and SectionedProfile), so a failure in one never
// appears under the other.
//
// The profile's own trail says how this version was made and when, from the fields the API returned
// (lib/profileTrails.ts); the three layout blocks draw their own (HeaderComposer, SectionOrderEditor and
// ShapeSettingsPanel with `showTrail`). Personal details is a form and has none.

export function ActiveProfile({
  version,
  onEdited,
  now,
}: {
  version: ProfileVersion;
  onEdited: () => Promise<void>;
  /** Only decides whether a date in a trail needs its year. */
  now: Date;
}) {
  const [view, setView] = useState<"sections" | "json">("sections");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const editor = useProfileEditor(version.canonical_json, onEdited);
  const personal = version.canonical_json.personal;
  const scholar = personal.links?.scholar;
  const jsonText = JSON.stringify(version.canonical_json, null, 2);
  const trail = activeProfileTrail(version, now);

  function exportJson() {
    downloadJson(version.canonical_json, "between-jobs-profile.json");
  }

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(jsonText);
    } catch {
      setCopyState("failed");
      return;
    }
    setCopyState("copied");
    setTimeout(() => setCopyState("idle"), 2000);
  }

  return (
    <>
      <DocumentBlock id="profile" trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle}>
        <section className="bj-prof-block bj-profile-identity" aria-labelledby="bj-profile-name">
          <div className="bj-card-header">
            <div className="bj-profile-identity-text">
              <h2 id="bj-profile-name" className="bj-profile-name" tabIndex={-1}>
                {personal.name ?? "Unnamed"}
              </h2>
              {personal.headline && <p className="bj-profile-headline">{personal.headline}</p>}
            </div>
            <span className="bj-badge-emerald">
              <span aria-hidden="true">{"✓"}</span> Active
            </span>
          </div>
          {scholar && (
            // The engine's header fields don't include Google Scholar yet -- a real, pre-existing gap in
            // the engine's header model, not something to paper over. Shown as a plain,
            // non-reorderable chip until the composer supports it.
            <div className="bj-contact-chips">
              <a className="bj-contact-chip" href={withProtocol(scholar)} target="_blank" rel="noreferrer">
                {scholar}
              </a>
            </div>
          )}
          <div className="bj-profile-bar">
            <ViewToggle
              label="View"
              options={[
                { value: "sections", label: "Sections" },
                { value: "json", label: "Raw JSON" },
              ]}
              value={view}
              onChange={setView}
            />
            <div className="bj-actions">
              <button type="button" onClick={exportJson}>
                Export JSON
              </button>
              {view === "json" && (
                <button type="button" onClick={() => void copyJson()}>
                  {copyState === "copied" ? "Copied ✓" : "Copy"}
                </button>
              )}
            </div>
          </div>
          {copyState === "failed" && (
            <p role="status" className="bj-muted bj-small">
              Couldn't copy to the clipboard. Your browser may be blocking it.
            </p>
          )}
          <p className="bj-muted bj-small">
            Activated {version.activated_at ? new Date(version.activated_at).toLocaleString() : ""}
          </p>
        </section>
      </DocumentBlock>

      <DocumentBlock id="personal-details">
        <PersonalDetailsCard personal={personal} onSave={editor.save} saving={editor.saving} />
      </DocumentBlock>

      <SectionChapter id="resume-layout" title="Resume layout">
        <p>
          Defaults for every resume Between Jobs generates; a specific application can override any of these. They shape the
          resume, not your saved profile data.
        </p>
      </SectionChapter>
      <DocumentBlock id="resume-header">
        <h3 className="bj-prof-subtitle" tabIndex={-1}>
          Header
        </h3>
        <HeaderComposer activeVersionId={version.id} showTrail />
      </DocumentBlock>
      <DocumentBlock id="resume-structure">
        <h3 className="bj-prof-subtitle" tabIndex={-1}>
          Resume structure
        </h3>
        <p className="bj-muted bj-small">
          Drag to reorder, or use the arrow buttons, or hide a section -- this controls the resume that gets generated, not
          your saved profile data.
        </p>
        <SectionOrderEditor profile={version.canonical_json} showTrail />
      </DocumentBlock>
      <DocumentBlock id="resume-settings">
        <h3 className="bj-prof-subtitle" tabIndex={-1}>
          Resume settings
        </h3>
        <p className="bj-muted bj-small">
          Defaults for every application -- a specific application can override any of these.
        </p>
        <ShapeSettingsPanel showTrail />
      </DocumentBlock>

      <SectionChapter id="profile-sections" title="Your profile">
        <p>Each section appears only when it has something in it. Every save is a new version.</p>
      </SectionChapter>
      <DocumentBlock id="profile-content">
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
          <pre className="bj-json-view" role="region" tabIndex={0} aria-label="Raw profile JSON">
            {jsonText}
          </pre>
        )}
      </DocumentBlock>
    </>
  );
}
