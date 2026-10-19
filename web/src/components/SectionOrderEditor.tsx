import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type { ResumeDocument } from "../lib/headerComposerTypes";
import { failedBlockTrail } from "../lib/profileTrails";
import { useFocusAfterRender } from "../lib/useFocusAfterRender";
import type { CanonicalProfile } from "../lib/profileTypes";
import { RENDERABLE_SECTIONS, SECTION_LABELS, type SectionName, sectionHasContent } from "../lib/sectionEditorTypes";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState } from "./paper/StateViews";
import "../styles/profile-studio.css";

// Section order and visibility -- which sections the generated resume has, and in what order.
// Deterministic, zero-LLM: unlike the Header Composer, no engine round trip is needed at all -- "does
// this section have content" is computable from the profile data the page already has, and there's no
// per-field display-mode/hyperlink logic to keep in sync with the LaTeX renderer, just a name, an
// order, and a boolean. Rows reorder by dragging AND with Move up / Move down buttons, which is how a
// keyboard or a touch screen reorders them.
//
// `applicationId`: same convention as HeaderComposer -- omitted -> the master/default document,
// provided -> that application's. `showTrail` draws the red-cross trail line when the read or the save
// failed (off by default, so an embedder that draws its own is unchanged); this block has nothing to
// attribute otherwise, since it is a setting and not a fetched or computed result.

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
  showTrail = false,
}: {
  profile: CanonicalProfile;
  applicationId?: string;
  showTrail?: boolean;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  // A moved row can lose the focus its button had, and a button at the end of the list is not disabled (a
  // disabled button cannot keep focus), so a press that can do nothing there leaves the keyboard where it was.
  const root = useRef<HTMLDivElement>(null);
  const focusAfter = useFocusAfterRender(root);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const path = applicationId ? `/resume-documents/mine?application_id=${applicationId}` : "/resume-documents/mine";
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

  async function persist(documentId: string, order: SectionName[], visibility: Record<string, boolean>) {
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
    return <LoadingState rows={4} rowHeight={44} label="Loading sections..." />;
  }
  if (state.kind === "error") {
    const failed = failedBlockTrail(state.message);
    return (
      <div>
        <FailureState message={state.message} onRetry={() => void load()} />
        {showTrail && <BlockTrail trail={failed.trail} />}
      </div>
    );
  }

  const { documentId, order, visibility, saving } = state;

  function reorder(from: number, to: number) {
    if (from === to || to < 0 || to >= order.length) return;
    const next = [...order];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    void persist(documentId, next, visibility);
  }

  function toggleVisible(section: SectionName) {
    void persist(documentId, order, { ...visibility, [section]: !visibility[section] });
  }

  return (
    <div className="bj-section-editor" ref={root}>
      <ul className="bj-section-editor-list" aria-label="Resume sections, in order">
        {order.map((section, i) => {
          const hasContent = sectionHasContent(section, profile);
          const visible = visibility[section] ?? false;
          const label = SECTION_LABELS[section];
          return (
            <li
              key={section}
              className={visible ? "bj-header-composer-row" : "bj-header-composer-row bj-section-hidden"}
              draggable
              onDragStart={() => setDragIndex(i)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => {
                if (dragIndex !== null) reorder(dragIndex, i);
                setDragIndex(null);
              }}
            >
              <span className="bj-drag-handle" aria-hidden="true">
                {"≡"}
              </span>
              <span className="bj-header-composer-field">{label}</span>
              {!hasContent && <span className="bj-muted bj-small">No content yet</span>}
              {hasContent && !visible && <span className="bj-muted bj-small">Hidden</span>}
              <label className="bj-section-editor-toggle">
                <input type="checkbox" checked={visible} disabled={!hasContent} aria-label={`Show ${label}`} onChange={() => toggleVisible(section)} />
                Show
              </label>
              <span className="bj-row-moves">
                <button
                  type="button"
                  data-focus-key={`up:${section}`}
                  onClick={() => {
                    if (i === 0) return;
                    focusAfter(`up:${section}`);
                    reorder(i, i - 1);
                  }}
                  aria-disabled={i === 0 ? true : undefined}
                  aria-label={`Move ${label} up`}
                >
                  <span aria-hidden="true">{"↑"}</span>
                </button>
                <button
                  type="button"
                  data-focus-key={`down:${section}`}
                  onClick={() => {
                    if (i === order.length - 1) return;
                    focusAfter(`down:${section}`);
                    reorder(i, i + 1);
                  }}
                  aria-disabled={i === order.length - 1 ? true : undefined}
                  aria-label={`Move ${label} down`}
                >
                  <span aria-hidden="true">{"↓"}</span>
                </button>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="bj-muted bj-small bj-studio-saving" role="status">
        {saving ? "Saving..." : ""}
      </p>
    </div>
  );
}
