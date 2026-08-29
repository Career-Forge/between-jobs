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

// Header Composer (Sprint 3.2c) -- Proposal §24.5.3. Deterministic,
// zero-LLM: every edit re-previews via POST /header/preview, which calls
// forge-engines' own resolve_header_chips -- the same logic the real
// generated artifact's header uses, not a second guess at it re-implemented
// here (the drift §24.5.8 rules out). Drag-and-drop is native HTML5
// (draggable/onDragOver/onDrop) -- no new dependency for a ~6-item list,
// matching this project's "no component library" convention.
//
// `applicationId` (Sprint 3.3d) selects which resume_document this edits --
// omitted -> the master/default document (Profile.tsx's own usage since
// Sprint 3.2c), provided -> that application's document (the workspace,
// Sprint 3.3d). Self-contained either way: it loads its own document, so
// the caller never threads document state through.

const DEFAULT_LAYOUT: Required<HeaderLayout> = {
  chips: ALL_HEADER_FIELDS.map((field) => ({ field, display_mode: "full" as ChipDisplayMode })),
  separator: "pipe",
};

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      documentId: string;
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
  const result = await apiFetch<{ chips: ResolvedChip[] }>(
    `/resume-documents/${documentId}/header/preview`,
    { method: "POST", body: JSON.stringify({ header_layout: layout }) },
  );
  return result.chips;
}

export function HeaderComposer({ applicationId }: { applicationId?: string } = {}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const path = applicationId
        ? `/resume-documents/mine?application_id=${applicationId}`
        : "/resume-documents/mine";
      const document = await apiFetch<ResumeDocument>(path);
      const layout = normalizeLayout(document.header_layout);
      const chips = await preview(document.id, layout);
      setState({ kind: "ready", documentId: document.id, layout, chips, saving: false });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function applyLayout(documentId: string, layout: Required<HeaderLayout>) {
    setState((prev) => (prev.kind === "ready" ? { ...prev, layout, saving: true } : prev));
    try {
      const chips = await preview(documentId, layout);
      setState({ kind: "ready", documentId, layout, chips, saving: true });
      await apiFetch(`/resume-documents/${documentId}/header`, {
        method: "PATCH",
        body: JSON.stringify({ header_layout: layout }),
      });
      setState({ kind: "ready", documentId, layout, chips, saving: false });
    } catch (e) {
      setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to save" });
    }
  }

  if (state.kind === "loading") {
    return <div className="bj-muted bj-small">Loading header...</div>;
  }
  if (state.kind === "error") {
    return (
      <div>
        <div className="bj-error">{state.message}</div>
        <button onClick={() => void load()}>Retry</button>
      </div>
    );
  }

  const { documentId, layout, chips, saving } = state;
  const activeFields = new Set(layout.chips.map((c) => c.field));
  const hiddenFields = ALL_HEADER_FIELDS.filter((f) => !activeFields.has(f));
  const hasLabelMode = layout.chips.some((c) => c.display_mode === "label");

  function reorder(from: number, to: number) {
    if (from === to) return;
    const next = [...layout.chips];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    void applyLayout(documentId, { ...layout, chips: next });
  }

  function setMode(field: string, mode: ChipDisplayMode) {
    const next = layout.chips.map((c) => (c.field === field ? { ...c, display_mode: mode } : c));
    void applyLayout(documentId, { ...layout, chips: next });
  }

  function remove(field: string) {
    void applyLayout(documentId, {
      ...layout,
      chips: layout.chips.filter((c) => c.field !== field),
    });
  }

  function add(field: string) {
    void applyLayout(documentId, {
      ...layout,
      chips: [...layout.chips, { field, display_mode: "full" }],
    });
  }

  return (
    <div className="bj-header-composer">
      <div className="bj-contact-chips">
        {chips.length === 0 && (
          <span className="bj-muted bj-small">No contact fields shown.</span>
        )}
        {chips.map((chip) =>
          chip.href ? (
            <a
              key={chip.field}
              className="bj-contact-chip"
              href={chip.href}
              target="_blank"
              rel="noreferrer"
            >
              {chip.text}
            </a>
          ) : (
            <span key={chip.field} className="bj-contact-chip">
              {chip.text}
            </span>
          ),
        )}
      </div>

      {hasLabelMode && (
        <div className="bj-warnings">
          <div>
            ⚠ A label-mode field shows its link text only on screen -- the URL is invisible on
            paper.
          </div>
        </div>
      )}

      <div className="bj-header-composer-list">
        {layout.chips.map((chip, i) => (
          <div
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
              ≡
            </span>
            <span className="bj-header-composer-field">
              {FIELD_LABELS[chip.field] ?? chip.field}
            </span>
            <select
              value={chip.display_mode ?? "full"}
              onChange={(e) => setMode(chip.field, e.target.value as ChipDisplayMode)}
            >
              <option value="full">Full</option>
              <option value="label">Label only</option>
            </select>
            <button onClick={() => remove(chip.field)} aria-label={`Remove ${chip.field}`}>
              Remove
            </button>
          </div>
        ))}
      </div>

      {hiddenFields.length > 0 && (
        <div className="bj-actions">
          {hiddenFields.map((field) => (
            <button key={field} onClick={() => add(field)}>
              + {FIELD_LABELS[field]}
            </button>
          ))}
        </div>
      )}

      {saving && <div className="bj-muted bj-small">Saving...</div>}
    </div>
  );
}
