import { useState } from "react";
import { EntryEditModal } from "./EntryEditModal";
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

// The sectioned Profile view (Sprint 3.1d), now with per-section editing
// (Sprint 3.1e) -- built to design-references/profile-sections-{engineer,
// academic}.png and profile-edit-versioned-modal.png. Every section
// renders ONLY when it has content -- a profile with publications and no
// experience (the PhD shape) looks like a complete, normal profile, not
// one with gaps. No profile-strength score or percentile anywhere
// (Proposal §38 -- that's gamification aimed at anxious job seekers, not
// this product).
//
// Editing is the versioned-import mechanic (useProfileEditor): every
// Save clones the current profile, applies the change, and re-imports +
// activates through the exact same deterministic validator a fresh
// upload goes through. Delete-the-last-piece-of-evidence gets the same
// honest rejection a from-scratch import would.
//
// The 8 "array of typed entries" sections (Experience/Projects/Education/
// Publications/Patents/Certifications/Languages/Volunteering) share one
// generic edit modal, config-driven (sectionFieldConfigs.ts). Achievements
// (strings) and Skills (categorized chip lists) are flat lists, not
// structured entries -- they get their own lighter inline editors.
// Personal/header editing is deliberately out of scope here (the Header
// Composer is its own, richer checkpoint).

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

// The 8 sections editable through the generic entry modal -- matches
// SECTION_FIELD_CONFIGS' keys exactly.
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

// R5 (resumeforge-shape-and-fit.md): only these 3 sections' entries actually
// reach forge-engines' allocator as RANKED, PINNABLE items -- pins float on
// a min_bullets floor, which a citation-style entry doesn't have. This is
// independent of whether a section RENDERS at all: publications/patents
// (R7, 2026-08-23) do reach a generated resume now, just via the simpler
// achievements-style "required, presence-gated" path, not bubbles/the
// allocator's ranking -- certifications/languages/volunteering are the
// ones still genuinely unwired end to end.
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
    <span className="bj-badge-pin" title="Always included in generated resumes">
      📌 Mandatory{pin.min_bullets ? ` · min ${pin.min_bullets}` : ""}
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

type EditTarget = { sectionKey: (typeof ENTRY_SECTION_KEYS)[number]; index: number | null };

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
  onSave: (mutate: (draft: CanonicalProfile) => CanonicalProfile) => Promise<void>;
  saving: boolean;
  error: string | null;
  clearError: () => void;
}) {
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null);

  const visibleSections = SECTION_ORDER.filter((key) => isNonEmpty(profile, key));
  const emptyEntrySections = ENTRY_SECTION_KEYS.filter((key) => !isNonEmpty(profile, key));

  function openEdit(sectionKey: EditTarget["sectionKey"], index: number) {
    clearError();
    setEditTarget({ sectionKey, index });
  }
  function openAdd(sectionKey: EditTarget["sectionKey"]) {
    clearError();
    setEditTarget({ sectionKey, index: null });
  }
  function closeModal() {
    setEditTarget(null);
  }

  async function handleDelete(sectionKey: EditTarget["sectionKey"], index: number) {
    if (!window.confirm(`Remove this ${SECTION_FIELD_CONFIGS[sectionKey].entryLabel.toLowerCase()}? This creates a new profile version.`)) {
      return;
    }
    try {
      await onSave((draft) => {
        const list = [...((draft[sectionKey] as unknown[] | undefined) ?? [])];
        list.splice(index, 1);
        return { ...draft, [sectionKey]: list };
      });
    } catch {
      // onSave already recorded the error; nothing else to do here.
    }
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
    <div className="bj-profile-layout">
      <div className="bj-profile-sections">
        {pinnedCount > 0 && (
          <div className="bj-muted bj-small">
            📌 {pinnedCount} of {MAX_PINNED_ENTRIES} mandatory slots used
          </div>
        )}
        {visibleSections.includes("experience") && (
          <ExperienceSection entries={profile.experience!} {...entryProps} onAdd={() => openAdd("experience")} />
        )}
        {visibleSections.includes("projects") && (
          <ProjectsSection entries={profile.projects!} {...entryProps} onAdd={() => openAdd("projects")} />
        )}
        {visibleSections.includes("publications") && (
          <PublicationsSection
            entries={profile.publications!}
            {...entryProps}
            onAdd={() => openAdd("publications")}
          />
        )}
        {visibleSections.includes("patents") && (
          <PatentsSection entries={profile.patents!} {...entryProps} onAdd={() => openAdd("patents")} />
        )}
        {visibleSections.includes("education") && (
          <EducationSection entries={profile.education!} {...entryProps} onAdd={() => openAdd("education")} />
        )}
        {visibleSections.includes("skills") && <SkillsSection skills={profile.skills!} onSave={onSave} />}
        {visibleSections.includes("certifications") && (
          <CertificationsSection
            entries={profile.certifications!}
            {...entryProps}
            onAdd={() => openAdd("certifications")}
          />
        )}
        {visibleSections.includes("achievements") && (
          <AchievementsSection entries={profile.achievements!} onSave={onSave} />
        )}
        {visibleSections.includes("languages") && (
          <LanguagesSection entries={profile.languages!} {...entryProps} onAdd={() => openAdd("languages")} />
        )}
        {visibleSections.includes("volunteering") && (
          <VolunteeringSection
            entries={profile.volunteering!}
            {...entryProps}
            onAdd={() => openAdd("volunteering")}
          />
        )}

        {emptyEntrySections.length > 0 && (
          <div className="bj-ghost-add-row">
            {emptyEntrySections.map((key) => (
              <button key={key} onClick={() => openAdd(key)}>
                + Add {SECTION_FIELD_CONFIGS[key].entryLabel.toLowerCase()}
              </button>
            ))}
          </div>
        )}
      </div>
      {visibleSections.length > 1 && (
        <nav className="bj-anchor-nav">
          {visibleSections.map((key) => (
            <a key={key} href={`#section-${key}`}>
              {SECTION_LABELS[key]}
            </a>
          ))}
        </nav>
      )}

      {editTarget && (
        <EntryEditModal
          config={SECTION_FIELD_CONFIGS[editTarget.sectionKey]}
          initialValues={
            editTarget.index === null
              ? {}
              : ((profile[editTarget.sectionKey] as unknown as Record<string, unknown>[])[
                  editTarget.index
                ] ?? {})
          }
          isNew={editTarget.index === null}
          canPin={(PINNABLE_SECTION_KEYS as readonly string[]).includes(editTarget.sectionKey)}
          pinnedCountExcludingThis={
            pinnedCount -
            (editTarget.index !== null &&
            (profile[editTarget.sectionKey] as unknown as { pin?: { mandatory: boolean } | null }[])[
              editTarget.index
            ]?.pin?.mandatory
              ? 1
              : 0)
          }
          maxPinned={MAX_PINNED_ENTRIES}
          versionLabel={`Saving creates version ${versionCount + 1}`}
          saving={saving}
          error={error}
          onSave={(values) => void handleModalSave(values)}
          onCancel={closeModal}
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
  children: React.ReactNode;
}) {
  return (
    <section id={`section-${id}`} className="bj-section-card">
      <div className="bj-section-heading">
        <h2>{title}</h2>
        <span className="bj-section-count">{count}</span>
        {onAdd && (
          <button className="bj-link-button bj-section-add" onClick={onAdd}>
            + Add {addLabel}
          </button>
        )}
      </div>
      {children}
    </section>
  );
}

function EntryActions({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="bj-entry-actions">
      <button className="bj-link-button" onClick={onEdit}>
        Edit
      </button>
      <button className="bj-link-button bj-link-button-danger" onClick={onDelete}>
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
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
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
    <div className="bj-metric-pills">
      {metrics.slice(0, 4).map((m) => (
        <span key={m} className="bj-metric-pill">
          {m}
        </span>
      ))}
    </div>
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

function ExperienceEntry({
  entry,
  onEdit,
  onDelete,
}: {
  entry: Experience;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = (entry.bullets?.length ?? 0) > 0 || (entry.skills?.length ?? 0) > 0;

  return (
    <div className="bj-timeline-entry">
      <div className="bj-timeline-rail">
        <span className="bj-timeline-node" />
      </div>
      <div className="bj-timeline-body">
        <div className="bj-timeline-head">
          <div>
            <h3>
              {entry.title} <PinnedBadge pin={entry.pin} />
            </h3>
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
            <button className="bj-link-button" onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Hide details" : `Show details${entry.bullets?.length ? ` (${entry.bullets.length})` : ""}`}
            </button>
          )}
          <EntryActions onEdit={onEdit} onDelete={onDelete} />
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
              <div className="bj-chip-row">
                {entry.skills.map((s) => (
                  <span key={s} className="bj-chip">
                    {s}
                  </span>
                ))}
              </div>
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

function ProjectEntry({
  entry,
  onEdit,
  onDelete,
}: {
  entry: Project;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = (entry.bullets?.length ?? 0) > 0;

  return (
    <div className="bj-project-card">
      <div className="bj-project-icon" aria-hidden="true">
        ◆
      </div>
      <div className="bj-project-body">
        <div className="bj-project-name">
          {entry.name} <PinnedBadge pin={entry.pin} />
        </div>
        {entry.tech && entry.tech.length > 0 && (
          <div className="bj-muted bj-small">{entry.tech.slice(0, 4).join(" · ")}</div>
        )}
        <MetricPills metrics={entry.metrics} />
        <div className="bj-entry-toolbar">
          {hasDetail && (
            <button className="bj-link-button" onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Hide details" : "Show details"}
            </button>
          )}
          <EntryActions onEdit={onEdit} onDelete={onDelete} />
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
    <SectionCard
      id="publications"
      title="Publications"
      count={entries.length}
      onAdd={onAdd}
      addLabel="publication"
    >
      <div className="bj-citation-list">
        {entries.map((entry, i) => (
          <div key={`${entry.title}-${i}`} className="bj-citation-row">
            <span className="bj-citation-index">{i + 1}</span>
            <div className="bj-citation-body">
              {entry.authors && <div className="bj-muted bj-small">{entry.authors}</div>}
              <div className="bj-citation-title">{entry.title}</div>
              <div className="bj-muted bj-small">
                {[entry.venue, entry.date].filter(Boolean).join(" · ")}
              </div>
              <EntryActions onEdit={() => onEdit("publications", i)} onDelete={() => void onDelete("publications", i)} />
            </div>
            {entry.url && (
              <a
                className="bj-link-chip"
                href={normalizeUrl(entry.url)}
                target="_blank"
                rel="noreferrer"
              >
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
            <span className="bj-citation-index">{i + 1}</span>
            <div className="bj-citation-body">
              {entry.patent_number && (
                <div className="bj-muted bj-small">{entry.patent_number}</div>
              )}
              <div className="bj-citation-title">{entry.title}</div>
              <div className="bj-muted bj-small">
                {[entry.status, entry.date].filter(Boolean).join(" · ")}
              </div>
              <EntryActions onEdit={() => onEdit("patents", i)} onDelete={() => void onDelete("patents", i)} />
            </div>
            {entry.url && (
              <a
                className="bj-link-chip"
                href={normalizeUrl(entry.url)}
                target="_blank"
                rel="noreferrer"
              >
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
        {entries.map((entry, i) => (
          <div key={`${entry.institution}-${i}`} className="bj-simple-row">
            <span className="bj-simple-icon" aria-hidden="true">
              🎓
            </span>
            <div className="bj-simple-body">
              <div className="bj-simple-title">
                {entry.degree}
                {entry.field ? `, ${entry.field}` : ""} <PinnedBadge pin={entry.pin} />
              </div>
              <div className="bj-muted bj-small">{entry.institution}</div>
              <EntryActions onEdit={() => onEdit("education", i)} onDelete={() => void onDelete("education", i)} />
            </div>
            <DateChip start={entry.start_date} end={entry.end_date} />
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function CertificationsSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<Certification>) {
  return (
    <SectionCard
      id="certifications"
      title="Certifications"
      count={entries.length}
      onAdd={onAdd}
      addLabel="certification"
    >
      <div className="bj-simple-list">
        {entries.map((entry, i) => (
          <div key={`${entry.name}-${i}`} className="bj-simple-row">
            <span className="bj-simple-icon" aria-hidden="true">
              ✓
            </span>
            <div className="bj-simple-body">
              <div className="bj-simple-title">{entry.name}</div>
              {entry.issuer && <div className="bj-muted bj-small">{entry.issuer}</div>}
              <EntryActions
                onEdit={() => onEdit("certifications", i)}
                onDelete={() => void onDelete("certifications", i)}
              />
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
      <div className="bj-chip-row">
        {entries.map((entry, i) => (
          <div key={`${entry.language}-${i}`} className="bj-language-chip">
            <div>{entry.language}</div>
            {entry.fluency && <div className="bj-muted bj-small">{entry.fluency}</div>}
            <EntryActions onEdit={() => onEdit("languages", i)} onDelete={() => void onDelete("languages", i)} />
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function VolunteeringSection({ entries, onEdit, onDelete, onAdd }: EntrySectionProps<VolunteerEntry>) {
  return (
    <SectionCard
      id="volunteering"
      title="Volunteering"
      count={entries.length}
      onAdd={onAdd}
      addLabel="volunteering entry"
    >
      <div className="bj-simple-list">
        {entries.map((entry, i) => (
          <div key={`${entry.organization}-${i}`} className="bj-simple-row">
            <span className="bj-simple-icon" aria-hidden="true">
              ♥
            </span>
            <div className="bj-simple-body">
              <div className="bj-simple-title">{entry.organization}</div>
              {entry.role && <div className="bj-muted bj-small">{entry.role}</div>}
              <EntryActions
                onEdit={() => onEdit("volunteering", i)}
                onDelete={() => void onDelete("volunteering", i)}
              />
            </div>
            <DateChip start={entry.start_date} end={entry.end_date} />
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function SkillsSection({
  skills,
  onSave,
}: {
  skills: Skills;
  onSave: (mutate: (draft: CanonicalProfile) => CanonicalProfile) => Promise<void>;
}) {
  const [draftByCategory, setDraftByCategory] = useState<Record<string, string>>({});
  const categories = (Object.keys(SKILL_CATEGORY_LABELS) as (keyof Skills)[]).filter(
    (key) => (skills[key]?.length ?? 0) > 0,
  );
  const allCategories = Object.keys(SKILL_CATEGORY_LABELS) as (keyof Skills)[];

  async function addSkill(category: keyof Skills) {
    const value = (draftByCategory[category] ?? "").trim();
    if (!value) return;
    await onSave((draft) => {
      const nextSkills = { ...(draft.skills ?? {}) };
      const list = nextSkills[category] ?? [];
      if (!list.includes(value)) {
        nextSkills[category] = [...list, value];
      }
      return { ...draft, skills: nextSkills };
    });
    setDraftByCategory((prev) => ({ ...prev, [category]: "" }));
  }

  async function removeSkill(category: keyof Skills, skill: string) {
    await onSave((draft) => {
      const nextSkills = { ...(draft.skills ?? {}) };
      nextSkills[category] = (nextSkills[category] ?? []).filter((s) => s !== skill);
      return { ...draft, skills: nextSkills };
    });
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
            <div key={key}>
              <div className="bj-skill-group-label">{SKILL_CATEGORY_LABELS[key]}</div>
              <div className="bj-chip-row">
                {list.map((s) => (
                  <span key={s} className="bj-chip bj-chip-removable">
                    {s}
                    <button onClick={() => void removeSkill(key, s)}>×</button>
                  </span>
                ))}
                <input
                  className="bj-inline-chip-input"
                  type="text"
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

function AchievementsSection({
  entries,
  onSave,
}: {
  entries: string[];
  onSave: (mutate: (draft: CanonicalProfile) => CanonicalProfile) => Promise<void>;
}) {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [newEntry, setNewEntry] = useState("");

  async function commitEdit(index: number) {
    const value = draft.trim();
    if (!value) return;
    await onSave((profile) => {
      const list = [...(profile.achievements ?? [])];
      list[index] = value;
      return { ...profile, achievements: list };
    });
    setEditingIndex(null);
  }

  async function removeAt(index: number) {
    await onSave((profile) => {
      const list = [...(profile.achievements ?? [])];
      list.splice(index, 1);
      return { ...profile, achievements: list };
    });
  }

  async function addNew() {
    const value = newEntry.trim();
    if (!value) return;
    await onSave((profile) => ({
      ...profile,
      achievements: [...(profile.achievements ?? []), value],
    }));
    setNewEntry("");
  }

  return (
    <SectionCard id="achievements" title="Achievements" count={entries.length}>
      <div className="bj-simple-list">
        {entries.map((entry, i) => (
          <div key={i} className="bj-simple-row">
            <span className="bj-simple-icon" aria-hidden="true">
              🏆
            </span>
            {editingIndex === i ? (
              <div className="bj-achievement-edit">
                <input type="text" value={draft} onChange={(e) => setDraft(e.target.value)} />
                <button className="bj-link-button" onClick={() => void commitEdit(i)}>
                  Save
                </button>
                <button className="bj-link-button" onClick={() => setEditingIndex(null)}>
                  Cancel
                </button>
              </div>
            ) : (
              <>
                <div className="bj-simple-body bj-achievement-text">{entry}</div>
                <EntryActions
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
          <button onClick={() => void addNew()} disabled={!newEntry.trim()}>
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
