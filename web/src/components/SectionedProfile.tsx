import { useRef, useState, type ReactNode } from "react";
import { EntryEditModal } from "./EntryEditModal";
import { saveFailureMessage } from "../lib/useProfileEditor";
import { SECTION_FIELD_CONFIGS } from "../lib/sectionFieldConfigs";
import type {
  CanonicalProfile,
  Certification,
  Education,
  Experience,
  LanguageEntry,
  Patent,
  Project,
  Publication,
  Skills,
  VolunteerEntry,
} from "../lib/profileTypes";
import { SKILL_CATEGORY_LABELS } from "../lib/profileTypes";
import "../styles/profile.css";

// The sectioned Profile view, with per-section editing. Every section renders ONLY when it has content --
// a profile with publications and no experience (the PhD shape) looks like a complete, normal profile,
// not one with gaps. No profile-strength score or percentile anywhere: that is gamification aimed at
// anxious job seekers, not this product.
//
// EDITING is the versioned-import mechanic (useProfileEditor): every Save clones the current profile,
// applies the change, and re-imports + activates through the exact same deterministic validator a fresh
// upload goes through, so it makes a new immutable version and deleting the last piece of evidence gets
// the same honest rejection a from-scratch import would. The 8 "array of typed entries" sections
// (Experience/Projects/Education/Publications/Patents/Certifications/Languages/Volunteering) share one
// generic edit modal, config-driven (sectionFieldConfigs.ts). Achievements (strings) and Skills
// (categorized chip lists) are flat lists and get lighter inline editors. Personal details and the
// resume header have their own cards.
//
// A FAILED SAVE IS ALWAYS SHOWN. The entry modal shows its own (the `error` prop, cleared each time it
// opens). Every other write -- adding or removing a skill, saving an achievement, removing an entry --
// has no modal to carry a failure, so each is caught here and the words go in an alert above the sections
// with a way to dismiss it. That message is this component's own, not the shared editor's `error`, which
// the Personal details card also writes to: a failure there must not appear under the sections.

const SECTION_ORDER = [
  "experience",
  "projects",
  "publications",
  "patents",
  "education",
  "skills",
  "certifications",
  "achievements",
  "languages",
  "volunteering",
] as const;

type SectionKey = (typeof SECTION_ORDER)[number];

const SECTION_LABELS: Record<SectionKey, string> = {
  experience: "Experience",
  projects: "Projects",
  publications: "Publications",
  patents: "Patents",
  education: "Education",
  skills: "Skills",
  certifications: "Certifications",
  achievements: "Achievements",
  languages: "Languages",
  volunteering: "Volunteering",
};

// The 8 sections editable through the generic entry modal -- matches SECTION_FIELD_CONFIGS' keys exactly.
const ENTRY_SECTION_KEYS = [
  "experience",
  "projects",
  "publications",
  "patents",
  "education",
  "certifications",
  "languages",
  "volunteering",
] as const;

// Only these 3 sections' entries actually reach the engine's allocator as RANKED, PINNABLE items -- pins
// float on a min_bullets floor, which a citation-style entry doesn't have. This is independent of
// whether a section RENDERS at all: publications/patents do reach a generated resume, just via the
// simpler achievements-style "required, presence-gated" path, not the allocator's ranking --
// certifications/languages/volunteering are the ones still genuinely unwired end to end.
const PINNABLE_SECTION_KEYS = ["experience", "projects", "education"] as const;
const MAX_PINNED_ENTRIES = 6;

function countPinned(profile: CanonicalProfile): number {
  return PINNABLE_SECTION_KEYS.reduce((total, key) => {
    const entries = (profile[key] as { pin?: { mandatory: boolean } | null }[] | undefined) ?? [];
    return total + entries.filter((e) => e.pin?.mandatory).length;
  }, 0);
}

function PinnedBadge({ pin }: { pin?: { mandatory: boolean; min_bullets?: number | null } | null }) {
  if (!pin?.mandatory) return null;
  return (
    <span className="bj-badge-muted bj-pin-badge" title="Always included in generated resumes">
      Mandatory{pin.min_bullets ? ` · min ${pin.min_bullets}` : ""}
    </span>
  );
}

function isNonEmpty(profile: CanonicalProfile, key: SectionKey): boolean {
  if (key === "skills") {
    const skills = profile.skills ?? {};
    return Object.values(skills).some((list) => (list?.length ?? 0) > 0);
  }
  const value = profile[key];
  return Array.isArray(value) && value.length > 0;
}

type EditTarget = {
  sectionKey: (typeof ENTRY_SECTION_KEYS)[number];
  index: number | null;
  // The button that opened the dialog, so focus can go back to it.
  opener: HTMLElement | null;
};

type Save = (mutate: (draft: CanonicalProfile) => CanonicalProfile) => Promise<void>;

export function SectionedProfile({
  profile,
  versionCount,
  onSave,
  saving,
  error,
  clearError,
}: {
  profile: CanonicalProfile;
  versionCount: number;
  onSave: Save;
  saving: boolean;
  error: string | null;
  clearError: () => void;
}) {
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null);
  // The button pressed last inside the sections: where focus goes back to when a dialog it opened closes.
  // Safari and Firefox on a Mac do not focus a button on click, so "what had focus" cannot be relied on.
  const lastButton = useRef<HTMLElement | null>(null);
  // The words for a failed write that has no modal (this component's own; see the file comment).
  const [actionError, setActionError] = useState<string | null>(null);

  const visibleSections = SECTION_ORDER.filter((key) => isNonEmpty(profile, key));
  const emptyEntrySections = ENTRY_SECTION_KEYS.filter((key) => !isNonEmpty(profile, key));

  // Runs a write that has no modal. A rejection is shown, not swallowed (and never left unhandled).
  const guarded: Save = async (mutate) => {
    setActionError(null);
    try {
      await onSave(mutate);
    } catch (e) {
      setActionError(saveFailureMessage(e));
      throw e;
    }
  };
  // For callers that cannot do anything with the rejection: it has been shown already.
  const quietly = (run: Promise<void>) => run.catch(() => undefined);

  function openEdit(sectionKey: EditTarget["sectionKey"], index: number) {
    clearError();
    setActionError(null);
    setEditTarget({ sectionKey, index, opener: lastButton.current });
  }
  function openAdd(sectionKey: EditTarget["sectionKey"]) {
    clearError();
    setActionError(null);
    setEditTarget({ sectionKey, index: null, opener: lastButton.current });
  }
  function closeModal() {
    setEditTarget(null);
  }

  async function handleDelete(sectionKey: EditTarget["sectionKey"], index: number) {
    if (!window.confirm(`Remove this ${SECTION_FIELD_CONFIGS[sectionKey].entryLabel.toLowerCase()}? This creates a new profile version.`)) {
      return;
    }
    await quietly(
      guarded((draft) => {
        const list = [...((draft[sectionKey] as unknown[] | undefined) ?? [])];
        list.splice(index, 1);
        return { ...draft, [sectionKey]: list };
      }),
    );
  }

  async function handleModalSave(values: Record<string, unknown>) {
    if (!editTarget) return;
    const { sectionKey, index } = editTarget;
    try {
      await onSave((draft) => {
        const list = [...((draft[sectionKey] as unknown[] | undefined) ?? [])];
        if (index === null) {
          list.push(values);
        } else {
          list[index] = values;
        }
        return { ...draft, [sectionKey]: list };
      });
      closeModal();
    } catch {
      // Error is surfaced via the `error` prop inside the still-open modal.
    }
  }

  const entryProps = { onEdit: openEdit, onDelete: handleDelete };
  const pinnedCount = countPinned(profile);

  return (
    <div
      className="bj-profile-sections"
      onClickCapture={(event) => {
        if (event.target instanceof Element) lastButton.current = event.target.closest("button");
      }}
    >
      {visibleSections.length > 1 && (
        <nav className="bj-anchor-nav" aria-label="Profile sections">
          {visibleSections.map((key) => (
            <a key={key} href={`#section-${key}`}>
              {SECTION_LABELS[key]}
            </a>
          ))}
        </nav>
      )}

      {actionError !== null && (
        <div className="bj-state bj-state--failure" role="alert">
          <p>
            <span className="bj-state-glyph" aria-hidden="true">
              {"✕"}
            </span>
            That change was not saved: {actionError}
          </p>
          <button type="button" onClick={() => setActionError(null)}>
            Dismiss
          </button>
        </div>
      )}
      <p className="bj-muted bj-small bj-studio-saving" role="status">
        {saving && editTarget === null ? "Saving a new version..." : ""}
      </p>

      {pinnedCount > 0 && (
        <p className="bj-muted bj-small">
          {pinnedCount} of {MAX_PINNED_ENTRIES} mandatory slots used
        </p>
      )}
      {visibleSections.includes("experience") && (
        <ExperienceSection entries={profile.experience!} {...entryProps} onAdd={() => openAdd("experience")} />
      )}
      {visibleSections.includes("projects") && (
        <ProjectsSection entries={profile.projects!} {...entryProps} onAdd={() => openAdd("projects")} />
      )}
      {visibleSections.includes("publications") && (
        <PublicationsSection entries={profile.publications!} {...entryProps} onAdd={() => openAdd("publications")} />
      )}
      {visibleSections.includes("patents") && (
        <PatentsSection entries={profile.patents!} {...entryProps} onAdd={() => openAdd("patents")} />
      )}
      {visibleSections.includes("education") && (
        <EducationSection entries={profile.education!} {...entryProps} onAdd={() => openAdd("education")} />
      )}
      {visibleSections.includes("skills") && <SkillsSection skills={profile.skills!} onSave={guarded} />}
      {visibleSections.includes("certifications") && (
        <CertificationsSection entries={profile.certifications!} {...entryProps} onAdd={() => openAdd("certifications")} />
      )}
      {visibleSections.includes("achievements") && <AchievementsSection entries={profile.achievements!} onSave={guarded} />}
      {visibleSections.includes("languages") && (
        <LanguagesSection entries={profile.languages!} {...entryProps} onAdd={() => openAdd("languages")} />
      )}
      {visibleSections.includes("volunteering") && (
        <VolunteeringSection entries={profile.volunteering!} {...entryProps} onAdd={() => openAdd("volunteering")} />
      )}

      {emptyEntrySections.length > 0 && (
        <div className="bj-ghost-add-row" role="group" aria-label="Add a section">
          {emptyEntrySections.map((key) => (
            <button type="button" key={key} onClick={() => openAdd(key)}>
              + Add {SECTION_FIELD_CONFIGS[key].entryLabel.toLowerCase()}
            </button>
          ))}
        </div>
      )}

      {editTarget && (
        <EntryEditModal
          config={SECTION_FIELD_CONFIGS[editTarget.sectionKey]}
          initialValues={
            editTarget.index === null
              ? {}
              : ((profile[editTarget.sectionKey] as unknown as Record<string, unknown>[])[editTarget.index] ?? {})
          }
          isNew={editTarget.index === null}
          canPin={(PINNABLE_SECTION_KEYS as readonly string[]).includes(editTarget.sectionKey)}
          pinnedCountExcludingThis={
            pinnedCount -
            (editTarget.index !== null &&
            (profile[editTarget.sectionKey] as unknown as { pin?: { mandatory: boolean } | null }[])[editTarget.index]?.pin?.mandatory
              ? 1
              : 0)
          }
          maxPinned={MAX_PINNED_ENTRIES}
          versionLabel={`Saving creates version ${versionCount + 1}`}
          saving={saving}
          error={error}
          onSave={(values) => void handleModalSave(values)}
          onCancel={closeModal}
          // The button that opened it, if it is still there (a saved edit can rename its entry and so
          // replace the button); otherwise the heading of the section, which takes focus.
          returnFocus={() => (editTarget.opener?.isConnected ? editTarget.opener : document.getElementById(`section-${editTarget.sectionKey}-title`))}
        />
      )}
    </div>
  );
}

function SectionCard({
  id,
  title,
  count,
  onAdd,
  addLabel,
  children,
}: {
  id: string;
  title: string;
  count: number;
  onAdd?: () => void;
  addLabel?: string;
  children: ReactNode;
}) {
  return (
    <section id={`section-${id}`} className="bj-section-card" aria-labelledby={`section-${id}-title`}>
      <div className="bj-section-heading">
        <h3 id={`section-${id}-title`} tabIndex={-1}>
          {title}
        </h3>
        <span className="bj-section-count">{count}</span>
        {onAdd && (
          <button type="button" className="bj-link-button bj-section-add" onClick={onAdd}>
            + Add {addLabel}
          </button>
        )}
      </div>
      {children}
    </section>
  );
}

// Edit and Delete for one entry. The visible words stay "Edit" and "Delete"; the accessible name says
// which entry, since a list of them is otherwise a row of identical buttons.
function EntryActions({ name, onEdit, onDelete }: { name: string; onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="bj-entry-actions">
      <button type="button" className="bj-link-button" aria-label={`Edit ${name}`} onClick={onEdit}>
        Edit
      </button>
      <button type="button" className="bj-link-button bj-link-button-danger" aria-label={`Delete ${name}`} onClick={onDelete}>
        Delete
      </button>
    </div>
  );
}

function formatMonth(value: string | undefined): string {
  if (!value) {
    return "";
  }
  if (value.toLowerCase() === "present") {
    return "Present";
  }
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) {
    return value;
  }
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const monthIndex = Number(match[2]) - 1;
  return `${months[monthIndex] ?? match[2]} ${match[1]}`;
}

function DateChip({ start, end }: { start?: string; end?: string }) {
  const startLabel = formatMonth(start);
  const endLabel = formatMonth(end);
  if (!startLabel && !endLabel) {
    return null;
  }
  return (
    <span className="bj-date-chip">
      {startLabel}
      {startLabel && endLabel ? " – " : ""}
      {endLabel}
    </span>
  );
}

function MetricPills({ metrics }: { metrics?: string[] }) {
  if (!metrics || metrics.length === 0) {
    return null;
  }
  return (
    <ul className="bj-metric-pills" aria-label="Metrics">
      {metrics.slice(0, 4).map((m) => (
        <li key={m} className="bj-metric-pill">
          {m}
        </li>
      ))}
    </ul>
  );
}

interface EntrySectionProps<T> {
  entries: T[];
  onEdit: (sectionKey: (typeof ENTRY_SECTION_KEYS)[number], index: number) => void;
  onDelete: (sectionKey: (typeof ENTRY_SECTION_KEYS)[number], index: number) => Promise<void>;
  onAdd: () => void;
}

function ExperienceSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<Experience>) {
  return (
    <SectionCard id="experience" title="Experience" count={entries.length} onAdd={onAdd} addLabel="experience">
      <div className="bj-timeline">
        {entries.map((entry, i) => (
          <ExperienceEntry
            key={`${entry.company}-${i}`}
            entry={entry}
            onEdit={() => onEdit("experience", i)}
            onDelete={() => void onDelete("experience", i)}
          />
        ))}
      </div>
    </SectionCard>
  );
}

function ExperienceEntry({ entry, onEdit, onDelete }: { entry: Experience; onEdit: () => void; onDelete: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = (entry.bullets?.length ?? 0) > 0 || (entry.skills?.length ?? 0) > 0;

  return (
    <div className="bj-timeline-entry">
      <div className="bj-timeline-rail" aria-hidden="true">
        <span className="bj-timeline-node" />
      </div>
      <div className="bj-timeline-body">
        <div className="bj-timeline-head">
          <div>
            <h4>
              {entry.title} <PinnedBadge pin={entry.pin} />
            </h4>
            <div className="bj-muted">
              {entry.company}
              {entry.location ? ` · ${entry.location}` : ""}
            </div>
          </div>
          <DateChip start={entry.start_date} end={entry.is_current ? "present" : entry.end_date} />
        </div>
        <MetricPills metrics={entry.metrics} />
        <div className="bj-entry-toolbar">
          {hasDetail && (
            <button type="button" className="bj-link-button" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Hide details" : `Show details${entry.bullets?.length ? ` (${entry.bullets.length})` : ""}`}
            </button>
          )}
          <EntryActions name={`${entry.title} at ${entry.company}`} onEdit={onEdit} onDelete={onDelete} />
        </div>
        {expanded && (
          <div className="bj-entry-detail">
            {entry.bullets && entry.bullets.length > 0 && (
              <ul>
                {entry.bullets.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            )}
            {entry.skills && entry.skills.length > 0 && (
              <ul className="bj-chip-row" aria-label="Skills used">
                {entry.skills.map((s) => (
                  <li key={s} className="bj-chip">
                    {s}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ProjectsSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<Project>) {
  return (
    <SectionCard id="projects" title="Projects" count={entries.length} onAdd={onAdd} addLabel="project">
      <div className="bj-project-grid">
        {entries.map((entry, i) => (
          <ProjectEntry
            key={`${entry.name}-${i}`}
            entry={entry}
            onEdit={() => onEdit("projects", i)}
            onDelete={() => void onDelete("projects", i)}
          />
        ))}
      </div>
    </SectionCard>
  );
}

function ProjectEntry({ entry, onEdit, onDelete }: { entry: Project; onEdit: () => void; onDelete: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = (entry.bullets?.length ?? 0) > 0;

  return (
    <div className="bj-project-card">
      <div className="bj-project-body">
        <h4 className="bj-project-name">
          {entry.name} <PinnedBadge pin={entry.pin} />
        </h4>
        {entry.tech && entry.tech.length > 0 && <div className="bj-muted bj-small">{entry.tech.slice(0, 4).join(" · ")}</div>}
        <MetricPills metrics={entry.metrics} />
        <div className="bj-entry-toolbar">
          {hasDetail && (
            <button type="button" className="bj-link-button" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Hide details" : "Show details"}
            </button>
          )}
          <EntryActions name={entry.name} onEdit={onEdit} onDelete={onDelete} />
        </div>
        {expanded && entry.bullets && (
          <ul>
            {entry.bullets.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        )}
        {entry.url && (
          <a href={normalizeUrl(entry.url)} target="_blank" rel="noreferrer">
            {entry.url}
          </a>
        )}
      </div>
    </div>
  );
}

function PublicationsSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<Publication>) {
  return (
    <SectionCard id="publications" title="Publications" count={entries.length} onAdd={onAdd} addLabel="publication">
      <div className="bj-citation-list">
        {entries.map((entry, i) => (
          <div key={`${entry.title}-${i}`} className="bj-citation-row">
            <span className="bj-citation-index" aria-hidden="true">
              {i + 1}
            </span>
            <div className="bj-citation-body">
              {entry.authors && <div className="bj-muted bj-small">{entry.authors}</div>}
              <div className="bj-citation-title">{entry.title}</div>
              <div className="bj-muted bj-small">{[entry.venue, entry.date].filter(Boolean).join(" · ")}</div>
              <EntryActions name={entry.title} onEdit={() => onEdit("publications", i)} onDelete={() => void onDelete("publications", i)} />
            </div>
            {entry.url && (
              <a className="bj-link-chip" href={normalizeUrl(entry.url)} target="_blank" rel="noreferrer" aria-label={`Link for ${entry.title}`}>
                Link
              </a>
            )}
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function PatentsSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<Patent>) {
  return (
    <SectionCard id="patents" title="Patents" count={entries.length} onAdd={onAdd} addLabel="patent">
      <div className="bj-citation-list">
        {entries.map((entry, i) => (
          <div key={`${entry.title}-${i}`} className="bj-citation-row">
            <span className="bj-citation-index" aria-hidden="true">
              {i + 1}
            </span>
            <div className="bj-citation-body">
              {entry.patent_number && <div className="bj-muted bj-small">{entry.patent_number}</div>}
              <div className="bj-citation-title">{entry.title}</div>
              <div className="bj-muted bj-small">{[entry.status, entry.date].filter(Boolean).join(" · ")}</div>
              <EntryActions name={entry.title} onEdit={() => onEdit("patents", i)} onDelete={() => void onDelete("patents", i)} />
            </div>
            {entry.url && (
              <a className="bj-link-chip" href={normalizeUrl(entry.url)} target="_blank" rel="noreferrer" aria-label={`Link for ${entry.title}`}>
                Link
              </a>
            )}
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function EducationSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<Education>) {
  return (
    <SectionCard id="education" title="Education" count={entries.length} onAdd={onAdd} addLabel="education">
      <div className="bj-simple-list">
        {entries.map((entry, i) => {
          const title = `${entry.degree}${entry.field ? `, ${entry.field}` : ""}`;
          return (
            <div key={`${entry.institution}-${i}`} className="bj-simple-row">
              <div className="bj-simple-body">
                <div className="bj-simple-title">
                  {title} <PinnedBadge pin={entry.pin} />
                </div>
                <div className="bj-muted bj-small">{entry.institution}</div>
                <EntryActions name={`${title}, ${entry.institution}`} onEdit={() => onEdit("education", i)} onDelete={() => void onDelete("education", i)} />
              </div>
              <DateChip start={entry.start_date} end={entry.end_date} />
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}

function CertificationsSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<Certification>) {
  return (
    <SectionCard id="certifications" title="Certifications" count={entries.length} onAdd={onAdd} addLabel="certification">
      <div className="bj-simple-list">
        {entries.map((entry, i) => (
          <div key={`${entry.name}-${i}`} className="bj-simple-row">
            <div className="bj-simple-body">
              <div className="bj-simple-title">{entry.name}</div>
              {entry.issuer && <div className="bj-muted bj-small">{entry.issuer}</div>}
              <EntryActions name={entry.name} onEdit={() => onEdit("certifications", i)} onDelete={() => void onDelete("certifications", i)} />
            </div>
            {entry.date && <span className="bj-date-chip">{entry.date}</span>}
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function LanguagesSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<LanguageEntry>) {
  return (
    <SectionCard id="languages" title="Languages" count={entries.length} onAdd={onAdd} addLabel="language">
      <div className="bj-chip-row bj-language-row">
        {entries.map((entry, i) => (
          <div key={`${entry.language}-${i}`} className="bj-language-chip">
            <div>{entry.language}</div>
            {entry.fluency && <div className="bj-muted bj-small">{entry.fluency}</div>}
            <EntryActions name={entry.language} onEdit={() => onEdit("languages", i)} onDelete={() => void onDelete("languages", i)} />
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function VolunteeringSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<VolunteerEntry>) {
  return (
    <SectionCard id="volunteering" title="Volunteering" count={entries.length} onAdd={onAdd} addLabel="volunteering entry">
      <div className="bj-simple-list">
        {entries.map((entry, i) => (
          <div key={`${entry.organization}-${i}`} className="bj-simple-row">
            <div className="bj-simple-body">
              <div className="bj-simple-title">{entry.organization}</div>
              {entry.role && <div className="bj-muted bj-small">{entry.role}</div>}
              <EntryActions name={entry.organization} onEdit={() => onEdit("volunteering", i)} onDelete={() => void onDelete("volunteering", i)} />
            </div>
            <DateChip start={entry.start_date} end={entry.end_date} />
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

// A write that was refused is shown by the parent (`guarded`), and rethrown so the code that was waiting
// for it (clearing a box after a save) does not carry on as if it had worked.
function SkillsSection({ skills, onSave }: { skills: Skills; onSave: Save }) {
  const [draftByCategory, setDraftByCategory] = useState<Record<string, string>>({});
  const categories = (Object.keys(SKILL_CATEGORY_LABELS) as (keyof Skills)[]).filter((key) => (skills[key]?.length ?? 0) > 0);
  const allCategories = Object.keys(SKILL_CATEGORY_LABELS) as (keyof Skills)[];

  async function addSkill(category: keyof Skills) {
    const value = (draftByCategory[category] ?? "").trim();
    if (!value) return;
    try {
      await onSave((draft) => {
        const nextSkills = { ...(draft.skills ?? {}) };
        const list = nextSkills[category] ?? [];
        if (!list.includes(value)) {
          nextSkills[category] = [...list, value];
        }
        return { ...draft, skills: nextSkills };
      });
    } catch {
      // Shown by the parent; the typed text stays so it can be sent again.
      return;
    }
    setDraftByCategory((prev) => ({ ...prev, [category]: "" }));
  }

  async function removeSkill(category: keyof Skills, skill: string) {
    try {
      await onSave((draft) => {
        const nextSkills = { ...(draft.skills ?? {}) };
        nextSkills[category] = (nextSkills[category] ?? []).filter((s) => s !== skill);
        return { ...draft, skills: nextSkills };
      });
    } catch {
      // Shown by the parent.
    }
  }

  return (
    <SectionCard
      id="skills"
      title="Skills"
      count={categories.reduce((sum, key) => sum + (skills[key]?.length ?? 0), 0)}
    >
      <div className="bj-skill-groups">
        {allCategories.map((key) => {
          const list = skills[key] ?? [];
          return (
            <div key={key} role="group" aria-label={SKILL_CATEGORY_LABELS[key]}>
              <div className="bj-skill-group-label" aria-hidden="true">
                {SKILL_CATEGORY_LABELS[key]}
              </div>
              <div className="bj-chip-row">
                {list.map((s) => (
                  <span key={s} className="bj-chip bj-chip-removable">
                    {s}
                    <button type="button" aria-label={`Remove ${s} from ${SKILL_CATEGORY_LABELS[key]}`} onClick={() => void removeSkill(key, s)}>
                      <span aria-hidden="true">{"×"}</span>
                    </button>
                  </span>
                ))}
                <input
                  className="bj-inline-chip-input"
                  type="text"
                  aria-label={`Add to ${SKILL_CATEGORY_LABELS[key]}`}
                  placeholder="+ add"
                  value={draftByCategory[key] ?? ""}
                  onChange={(e) => setDraftByCategory((prev) => ({ ...prev, [key]: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === ",") {
                      e.preventDefault();
                      void addSkill(key);
                    }
                  }}
                  onBlur={() => void addSkill(key)}
                />
              </div>
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}

function AchievementsSection({ entries, onSave }: { entries: string[]; onSave: Save }) {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [newEntry, setNewEntry] = useState("");

  async function commitEdit(index: number) {
    const value = draft.trim();
    if (!value) return;
    try {
      await onSave((profile) => {
        const list = [...(profile.achievements ?? [])];
        list[index] = value;
        return { ...profile, achievements: list };
      });
    } catch {
      // Shown by the parent; the edit stays open with its text.
      return;
    }
    setEditingIndex(null);
  }

  async function removeAt(index: number) {
    try {
      await onSave((profile) => {
        const list = [...(profile.achievements ?? [])];
        list.splice(index, 1);
        return { ...profile, achievements: list };
      });
    } catch {
      // Shown by the parent.
    }
  }

  async function addNew() {
    const value = newEntry.trim();
    if (!value) return;
    try {
      await onSave((profile) => ({
        ...profile,
        achievements: [...(profile.achievements ?? []), value],
      }));
    } catch {
      // Shown by the parent; the typed text stays.
      return;
    }
    setNewEntry("");
  }

  return (
    <SectionCard id="achievements" title="Achievements" count={entries.length}>
      <div className="bj-simple-list">
        {entries.map((entry, i) => (
          <div key={i} className="bj-simple-row">
            {editingIndex === i ? (
              <div className="bj-achievement-edit">
                <input type="text" aria-label={`Achievement ${i + 1}`} value={draft} onChange={(e) => setDraft(e.target.value)} />
                <button type="button" className="bj-link-button" onClick={() => void commitEdit(i)}>
                  Save
                </button>
                <button type="button" className="bj-link-button" onClick={() => setEditingIndex(null)}>
                  Cancel
                </button>
              </div>
            ) : (
              <>
                <div className="bj-simple-body bj-achievement-text">{entry}</div>
                <EntryActions
                  name={`achievement ${i + 1}`}
                  onEdit={() => {
                    setDraft(entry);
                    setEditingIndex(i);
                  }}
                  onDelete={() => void removeAt(i)}
                />
              </>
            )}
          </div>
        ))}
        <div className="bj-achievement-add">
          <input
            type="text"
            aria-label="Add an achievement"
            placeholder="Add an achievement..."
            value={newEntry}
            onChange={(e) => setNewEntry(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void addNew();
              }
            }}
          />
          <button type="button" onClick={() => void addNew()} disabled={!newEntry.trim()}>
            Add
          </button>
        </div>
      </div>
    </SectionCard>
  );
}

function normalizeUrl(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}
