import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
import {
  ALL_HEADER_FIELDS,
  FIELD_LABELS,
  type ChipDisplayMode,
  type HeaderLayout,
  type ResolvedChip,
  type ResumeDocument,
} from "../lib/headerComposerTypes";
import { STALE_HEADER_NOTE, failedBlockTrail, headerTrail, isHeaderPreviewStale } from "../lib/profileTrails";
import { failureOf, type SetupNotice } from "../lib/setupRequired";
import { BlockTrail } from "./paper/DocumentLayout";
import { FailureState, LoadingState } from "./paper/StateViews";
import { SetupRequiredNotice } from "./SetupRequiredNotice";
import "../styles/profile-studio.css";

// Header Composer. Deterministic, zero-LLM: every edit re-previews via POST /header/preview, which
// calls the engine service's own resolve_header_chips -- the same logic the real generated artifact's
// header uses, not a second guess at it re-implemented here. Rows reorder by dragging (native HTML5
// drag-and-drop, no dependency for a six-item list) AND with Move up / Move down buttons, which is how
// a keyboard or a touch screen reorders them.
//
// `applicationId` selects which resume_document this edits -- omitted -> the master/default document
// (the Profile page's usage), provided -> that application's document (the workspace). Self-contained
// either way: it loads its own document, so the caller never threads document state through.
//
// Two optional props, both off by default so an embedder that draws its own trail is unchanged:
//   showTrail        draws this block's trail line (BlockTrail: in the margin inside a DocumentLayout,
//                    under the block otherwise) -- "Chips resolved by code", or the red cross and the
//                    failure's own words when the read or the save failed
//   activeVersionId  the id of the profile version in use. The chips come from the version the resume
//                    document was created against, which a later activation never moves, so when the two
//                    differ the preview says it uses an earlier version (section 14.4 "Stale")

const DEFAULT_LAYOUT: Required<HeaderLayout> = {
  chips: ALL_HEADER_FIELDS.map((field) => ({ field, display_mode: "full" as ChipDisplayMode })),
  separator: "pipe",
};

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  // No active profile yet (the document is made from one): the notice links to the page
  // that fixes it. No Retry: asking again cannot succeed until it is fixed.
  | { kind: "setup"; notice: SetupNotice }
  | {
      kind: "ready";
      documentId: string;
      // The profile version the document was created against, when the API says.
      documentVersionId: string | undefined;
      layout: Required<HeaderLayout>;
      chips: ResolvedChip[];
      saving: boolean;
    };

function normalizeLayout(layout: HeaderLayout | null | undefined): Required<HeaderLayout> {
  if (!layout?.chips || layout.chips.length === 0) {
    return DEFAULT_LAYOUT;
  }
  return { chips: layout.chips, separator: layout.separator ?? "pipe" };
}

async function preview(documentId: string, layout: HeaderLayout): Promise<ResolvedChip[]> {
  const result = await apiFetch<{ chips: ResolvedChip[] }>(`/resume-documents/${documentId}/header/preview`, {
    method: "POST",
    body: JSON.stringify({ header_layout: layout }),
  });
  return result.chips;
}

export function HeaderComposer({ applicationId, activeVersionId, showTrail = false }: { applicationId?: string; activeVersionId?: string; showTrail?: boolean } = {}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const path = applicationId ? `/resume-documents/mine?application_id=${applicationId}` : "/resume-documents/mine";
      const document = await apiFetch<ResumeDocument>(path);
      const layout = normalizeLayout(document.header_layout);
      const chips = await preview(document.id, layout);
      setState({ kind: "ready", documentId: document.id, documentVersionId: document.profile_version_id, layout, chips, saving: false });
    } catch (e) {
      setState(failureOf(e, "Failed to load"));
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function applyLayout(documentId: string, documentVersionId: string | undefined, layout: Required<HeaderLayout>) {
    setState((prev) => (prev.kind === "ready" ? { ...prev, layout, saving: true } : prev));
    try {
      const chips = await preview(documentId, layout);
      setState({ kind: "ready", documentId, documentVersionId, layout, chips, saving: true });
      await apiFetch(`/resume-documents/${documentId}/header`, {
        method: "PATCH",
        body: JSON.stringify({ header_layout: layout }),
      });
      setState({ kind: "ready", documentId, documentVersionId, layout, chips, saving: false });
    } catch (e) {
      setState(failureOf(e, "Failed to save"));
    }
  }

  if (state.kind === "loading") {
    return <LoadingState rows={2} rowHeight={44} label="Loading header..." />;
  }
  if (state.kind === "setup") {
    return <SetupRequiredNotice notice={state.notice} />;
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

  const { documentId, documentVersionId, layout, chips, saving } = state;
  const activeFields = new Set(layout.chips.map((c) => c.field));
  const hiddenFields = ALL_HEADER_FIELDS.filter((f) => !activeFields.has(f));
  const hasLabelMode = layout.chips.some((c) => c.display_mode === "label");
  const stale = isHeaderPreviewStale(documentVersionId, activeVersionId);
  const trail = headerTrail(documentVersionId, chips.length);

  function reorder(from: number, to: number) {
    if (from === to || to < 0 || to >= layout.chips.length) return;
    const next = [...layout.chips];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    void applyLayout(documentId, documentVersionId, { ...layout, chips: next });
  }

  function setMode(field: string, mode: ChipDisplayMode) {
    const next = layout.chips.map((c) => (c.field === field ? { ...c, display_mode: mode } : c));
    void applyLayout(documentId, documentVersionId, { ...layout, chips: next });
  }

  function remove(field: string) {
    void applyLayout(documentId, documentVersionId, { ...layout, chips: layout.chips.filter((c) => c.field !== field) });
  }

  function add(field: string) {
    void applyLayout(documentId, documentVersionId, { ...layout, chips: [...layout.chips, { field, display_mode: "full" }] });
  }

  return (
    <div className="bj-header-composer">
      <div className="bj-contact-chips" role="group" aria-label="Header preview">
        {chips.length === 0 && <span className="bj-muted bj-small">No contact fields shown.</span>}
        {chips.map((chip) =>
          chip.href ? (
            <a key={chip.field} className="bj-contact-chip" href={chip.href} target="_blank" rel="noreferrer">
              {chip.text}
            </a>
          ) : (
            <span key={chip.field} className="bj-contact-chip">
              {chip.text}
            </span>
          ),
        )}
      </div>

      {stale && <p className="bj-muted bj-small bj-studio-note">{STALE_HEADER_NOTE}</p>}

      {hasLabelMode && (
        <p className="bj-muted bj-small bj-studio-note">
          Note: a label-mode field shows its link text only on screen -- the URL is invisible on paper.
        </p>
      )}

      <ul className="bj-header-composer-list" aria-label="Header fields, in order">
        {layout.chips.map((chip, i) => {
          const label = FIELD_LABELS[chip.field] ?? chip.field;
          return (
            <li
              key={chip.field}
              className="bj-header-composer-row"
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
              <select aria-label={`How ${label} is shown`} value={chip.display_mode ?? "full"} onChange={(e) => setMode(chip.field, e.target.value as ChipDisplayMode)}>
                <option value="full">Full</option>
                <option value="label">Label only</option>
              </select>
              <span className="bj-row-moves">
                <button type="button" onClick={() => reorder(i, i - 1)} disabled={i === 0} aria-label={`Move ${label} up`}>
                  <span aria-hidden="true">{"↑"}</span>
                </button>
                <button type="button" onClick={() => reorder(i, i + 1)} disabled={i === layout.chips.length - 1} aria-label={`Move ${label} down`}>
                  <span aria-hidden="true">{"↓"}</span>
                </button>
                <button type="button" onClick={() => remove(chip.field)} aria-label={`Remove ${label}`}>
                  Remove
                </button>
              </span>
            </li>
          );
        })}
      </ul>

      {hiddenFields.length > 0 && (
        <div className="bj-actions">
          {hiddenFields.map((field) => (
            <button type="button" key={field} onClick={() => add(field)}>
              + {FIELD_LABELS[field]}
            </button>
          ))}
        </div>
      )}

      <p className="bj-muted bj-small bj-studio-saving" role="status">
        {saving ? "Saving..." : ""}
      </p>

      {showTrail && <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />}
    </div>
  );
}
