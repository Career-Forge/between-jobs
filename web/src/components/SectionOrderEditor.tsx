import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
import type { ResumeDocument } from "../lib/headerComposerTypes";
import type { CanonicalProfile } from "../lib/profileTypes";
import {
  RENDERABLE_SECTIONS,
  SECTION_LABELS,
  type SectionName,
  sectionHasContent,
} from "../lib/sectionEditorTypes";

// Section drag/hide (Sprint 3.2d) -- Proposal §24.5.2's Structure mode.
// Deterministic, zero-LLM: unlike the Header Composer, no forge-engines
// round trip is needed at all -- "does this section have content" is
// computable from the profile data the page already has, and there's no
// per-field display-mode/hyperlink logic to keep in sync with the LaTeX
// renderer, just a name, an order, and a boolean.
//
// `applicationId` (Sprint 3.3d): same convention as HeaderComposer --
// omitted -> the master/default document, provided -> that application's.

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      documentId: string;
      order: SectionName[];
      visibility: Record<string, boolean>;
      saving: boolean;
    };

function defaultVisibility(profile: CanonicalProfile): Record<string, boolean> {
  const visibility: Record<string, boolean> = {};
  for (const section of RENDERABLE_SECTIONS) {
    visibility[section] = sectionHasContent(section, profile);
  }
  return visibility;
}

function normalizeOrder(stored: string[]): SectionName[] {
  const known = new Set<string>(RENDERABLE_SECTIONS);
  const valid = stored.filter((s): s is SectionName => known.has(s));
  if (valid.length === 0) {
    return [...RENDERABLE_SECTIONS];
  }
  // A section that gained content (or was added to the vocabulary) after
  // the order was last customized is appended, never silently dropped.
  const missing = RENDERABLE_SECTIONS.filter((s) => !valid.includes(s));
  return [...valid, ...missing];
}

export function SectionOrderEditor({
  profile,
  applicationId,
}: {
  profile: CanonicalProfile;
  applicationId?: string;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const path = applicationId
        ? `/resume-documents/mine?application_id=${applicationId}`
        : "/resume-documents/mine";
      const document = await apiFetch<ResumeDocument>(path);
      const order = normalizeOrder(document.section_order ?? []);
      const hasSaved = Object.keys(document.section_visibility ?? {}).length > 0;
      const visibility = hasSaved
        ? { ...defaultVisibility(profile), ...document.section_visibility }
        : defaultVisibility(profile);
      setState({ kind: "ready", documentId: document.id, order, visibility, saving: false });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
    }
  }, [profile, applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function persist(
    documentId: string,
    order: SectionName[],
    visibility: Record<string, boolean>,
  ) {
    setState({ kind: "ready", documentId, order, visibility, saving: true });
    try {
      await apiFetch(`/resume-documents/${documentId}/sections`, {
        method: "PATCH",
        body: JSON.stringify({ section_order: order, section_visibility: visibility }),
      });
      setState({ kind: "ready", documentId, order, visibility, saving: false });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to save" });
    }
  }

  if (state.kind === "loading") {
    return <div className="bj-muted bj-small">Loading sections...</div>;
  }
  if (state.kind === "error") {
    return (
      <div>
        <div className="bj-error">{state.message}</div>
        <button onClick={() => void load()}>Retry</button>
      </div>
    );
  }

  const { documentId, order, visibility, saving } = state;

  function reorder(from: number, to: number) {
    if (from === to) return;
    const next = [...order];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    void persist(documentId, next, visibility);
  }

  function toggleVisible(section: SectionName) {
    void persist(documentId, order, { ...visibility, [section]: !visibility[section] });
  }

  return (
    <div className="bj-section-editor">
      <div className="bj-section-editor-list">
        {order.map((section, i) => {
          const hasContent = sectionHasContent(section, profile);
          const visible = visibility[section] ?? false;
          return (
            <div
              key={section}
              className={
                visible ? "bj-header-composer-row" : "bj-header-composer-row bj-section-hidden"
              }
              draggable
              onDragStart={() => setDragIndex(i)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => {
                if (dragIndex !== null) reorder(dragIndex, i);
                setDragIndex(null);
              }}
            >
              <span className="bj-drag-handle" aria-hidden="true">
                ≡
              </span>
              <span className="bj-header-composer-field">{SECTION_LABELS[section]}</span>
              {!hasContent && <span className="bj-muted bj-small">No content yet</span>}
              <label className="bj-section-editor-toggle">
                <input
                  type="checkbox"
                  checked={visible}
                  disabled={!hasContent}
                  onChange={() => toggleVisible(section)}
                />
                Show
              </label>
            </div>
          );
        })}
      </div>
      {saving && <div className="bj-muted bj-small">Saving...</div>}
    </div>
  );
}
