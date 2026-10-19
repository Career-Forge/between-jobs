import { useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useModalDialog } from "../lib/useModalDialog";
import type { FieldConfig, SectionFieldConfig } from "../lib/sectionFieldConfigs";
import "../styles/profile-studio.css";

// Generic entry editor -- one modal, driven by a per-section field config, used for every "array of
// typed objects" section (Experience, Projects, Education, Publications, Certifications, Languages,
// Volunteering). Drawn after the versioned-edit dialog design, with two deliberate
// deviations from that render: no "Location type" field (invented in the mockup -- it doesn't exist in
// the schema, so it doesn't exist here), and bullet reordering is up/down buttons instead of
// drag-and-drop (a button works from the keyboard and on a phone).
//
// IT IS A REAL DIALOG. It is drawn into <body> so the rest of the page can be made inert while it is
// open; focus moves to its first field and is kept inside it (Tab and Shift+Tab wrap); Esc cancels --
// not while a save is out -- and focus goes back to the control that opened it, or to where `returnFocus`
// says (lib/useModalDialog.ts).
// Nothing in it is cyan: the page's one cyan fill is the next-step band's button, so Save is an
// outlined button like every other.
//
// The footer says what saving does -- `versionLabel`, "Saving creates version 4" -- because an edit is
// a new immutable version of the profile, never a change in place (lib/useProfileEditor.ts).

type EntryValues = Record<string, unknown>;
type PinValue = { mandatory: boolean; min_bullets?: number | null } | null | undefined;

export function EntryEditModal({
  config,
  initialValues,
  isNew,
  canPin = false,
  pinnedCountExcludingThis = 0,
  maxPinned = 6,
  versionLabel,
  saving,
  error,
  onSave,
  onCancel,
  returnFocus,
}: {
  config: SectionFieldConfig;
  initialValues: EntryValues;
  isNew: boolean;
  // Only experience/projects/education reach the engine's allocator as pinnable -- the parent
  // decides which sections qualify, this modal just renders (or doesn't) the toggle.
  canPin?: boolean;
  pinnedCountExcludingThis?: number;
  maxPinned?: number;
  versionLabel: string;
  saving: boolean;
  error: string | null;
  onSave: (values: EntryValues) => void;
  onCancel: () => void;
  // Where focus goes when it closes, if the page knows better than "what had focus when it opened".
  returnFocus?: () => HTMLElement | null;
}) {
  const [values, setValues] = useState<EntryValues>(initialValues);
  const dialog = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useModalDialog(dialog, {
    onClose: onCancel,
    canClose: !saving,
    // The first thing to type in, not the close mark that comes first in the markup.
    initialFocus: () => body.current?.querySelector<HTMLElement>("input:not([type='checkbox']), textarea, select") ?? null,
    returnFocus,
  });

  function setField(key: string, value: unknown) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  const missingRequired = config.fields.some((f) => f.required && !String(values[f.key] ?? "").trim());
  const pin = values.pin as PinValue;
  const isPinned = Boolean(pin?.mandatory);
  const atPinLimit = !isPinned && pinnedCountExcludingThis >= maxPinned;

  function setMandatory(checked: boolean) {
    setField("pin", checked ? { mandatory: true, min_bullets: pin?.min_bullets ?? null } : null);
  }
  function setMinBullets(raw: string) {
    const n = raw.trim() === "" ? null : Math.max(1, Math.min(6, Number(raw) || 1));
    setField("pin", { mandatory: true, min_bullets: n });
  }

  const title = `${isNew ? "Add" : "Edit"} ${config.entryLabel.toLowerCase()}`;

  return createPortal(
    <div className="bj-modal-backdrop">
      <div ref={dialog} className="bj-modal bj-entry-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="bj-modal-header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="bj-modal-close" onClick={onCancel} disabled={saving} aria-label="Close">
            <span aria-hidden="true">{"×"}</span>
          </button>
        </div>
        <div ref={body} className="bj-modal-body">
          {config.fields.map((field) => (
            <FieldRenderer key={field.key} field={field} values={values} onChange={setField} />
          ))}
          {canPin && (
            <div className="bj-field bj-pin-field">
              <label className="bj-current-checkbox">
                <input type="checkbox" checked={isPinned} disabled={atPinLimit} onChange={(e) => setMandatory(e.target.checked)} />
                Mandatory -- always include this in generated resumes
              </label>
              {isPinned && (
                <label className="bj-field bj-pin-min">
                  <span>Minimum bullets (optional, 1-6)</span>
                  <input
                    type="number"
                    min={1}
                    max={6}
                    value={pin?.min_bullets ?? ""}
                    placeholder="No minimum"
                    onChange={(e) => setMinBullets(e.target.value)}
                  />
                </label>
              )}
              {atPinLimit && (
                <div className="bj-muted bj-small">You've used all {maxPinned} mandatory slots -- unmark another entry first.</div>
              )}
            </div>
          )}
          {error && (
            <div className="bj-error" role="alert">
              {error}
            </div>
          )}
        </div>
        <div className="bj-modal-footer">
          <span className="bj-muted bj-small">{versionLabel}</span>
          <div className="bj-actions">
            <button type="button" onClick={onCancel} disabled={saving}>
              Cancel
            </button>
            <button type="button" className="bj-dialog-confirm" onClick={() => onSave(values)} disabled={saving || missingRequired}>
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function FieldRenderer({
  field,
  values,
  onChange,
}: {
  field: FieldConfig;
  values: EntryValues;
  onChange: (key: string, value: unknown) => void;
}) {
  switch (field.type) {
    case "text":
      return (
        <label className="bj-field">
          <span>
            {field.label}
            {field.required && " *"}
          </span>
          <input
            type="text"
            value={String(values[field.key] ?? "")}
            placeholder={field.placeholder}
            required={field.required}
            onChange={(e) => onChange(field.key, e.target.value)}
          />
        </label>
      );
    case "bullets":
      return (
        <BulletsField
          label={field.label}
          value={(values[field.key] as string[] | undefined) ?? []}
          onChange={(v) => onChange(field.key, v)}
        />
      );
    case "chips":
      return (
        <ChipsField
          label={field.label}
          placeholder={field.placeholder}
          value={(values[field.key] as string[] | undefined) ?? []}
          onChange={(v) => onChange(field.key, v)}
        />
      );
    case "daterange":
      return <DateRangeField field={field} values={values} onChange={onChange} />;
    default:
      return null;
  }
}

function BulletsField({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  const groupId = useId();
  function update(i: number, text: string) {
    const next = [...value];
    next[i] = text;
    onChange(next);
  }
  function remove(i: number) {
    onChange(value.filter((_, idx) => idx !== i));
  }
  function move(i: number, delta: number) {
    const j = i + delta;
    if (j < 0 || j >= value.length) return;
    const next = [...value];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  }

  return (
    <div className="bj-field" role="group" aria-labelledby={groupId}>
      <span id={groupId}>{label}</span>
      <div className="bj-bullets-editor">
        {value.map((bullet, i) => (
          <div key={i} className="bj-bullet-row">
            <textarea rows={2} aria-label={`${label} ${i + 1}`} value={bullet} onChange={(e) => update(i, e.target.value)} />
            <div className="bj-bullet-row-actions">
              <button type="button" onClick={() => move(i, -1)} disabled={i === 0} aria-label={`Move ${label.toLowerCase()} ${i + 1} up`}>
                <span aria-hidden="true">{"↑"}</span>
              </button>
              <button type="button" onClick={() => move(i, 1)} disabled={i === value.length - 1} aria-label={`Move ${label.toLowerCase()} ${i + 1} down`}>
                <span aria-hidden="true">{"↓"}</span>
              </button>
              <button type="button" onClick={() => remove(i)} aria-label={`Remove ${label.toLowerCase()} ${i + 1}`}>
                <span aria-hidden="true">{"✕"}</span>
              </button>
            </div>
          </div>
        ))}
        <button type="button" onClick={() => onChange([...value, ""])}>
          + Add bullet
        </button>
      </div>
    </div>
  );
}

function ChipsField({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder?: string;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function commit() {
    const trimmed = draft.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
    }
    setDraft("");
  }

  return (
    <div className="bj-field">
      <span>{label}</span>
      <div className="bj-chip-row">
        {value.map((chip) => (
          <span key={chip} className="bj-chip bj-chip-removable">
            {chip}
            <button type="button" aria-label={`Remove ${chip}`} onClick={() => onChange(value.filter((c) => c !== chip))}>
              <span aria-hidden="true">{"×"}</span>
            </button>
          </span>
        ))}
      </div>
      <input
        type="text"
        aria-label={`Add to ${label.toLowerCase()}`}
        value={draft}
        placeholder={placeholder ?? "Type and press Enter"}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          }
        }}
        onBlur={commit}
      />
    </div>
  );
}

const MONTHS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"];
const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parseMonthYear(value: string | undefined): { month: string; year: string } {
  const match = /^(\d{4})-(\d{2})$/.exec(value ?? "");
  return match ? { year: match[1], month: match[2] } : { year: "", month: "" };
}

function DateRangeField({
  field,
  values,
  onChange,
}: {
  field: FieldConfig;
  values: EntryValues;
  onChange: (key: string, value: unknown) => void;
}) {
  const groupId = useId();
  const startKey = field.startKey!;
  const endKey = field.endKey!;
  const currentKey = field.currentKey;
  const isCurrent = currentKey ? Boolean(values[currentKey]) : false;
  const start = parseMonthYear(values[startKey] as string | undefined);
  const endRaw = values[endKey] as string | undefined;
  const end = parseMonthYear(endRaw === "present" ? undefined : endRaw);

  function setStart(month: string, year: string) {
    onChange(startKey, month && year ? `${year}-${month}` : "");
  }
  function setEnd(month: string, year: string) {
    onChange(endKey, month && year ? `${year}-${month}` : "");
  }
  function setCurrent(checked: boolean) {
    if (currentKey) onChange(currentKey, checked);
    onChange(endKey, checked ? "present" : "");
  }

  return (
    <div className="bj-field" role="group" aria-labelledby={groupId}>
      <span id={groupId}>{field.label}</span>
      <div className="bj-daterange">
        <div className="bj-daterange-group">
          <select
            aria-label="Start month"
            value={start.month}
            onChange={(e) => setStart(e.target.value, start.year || String(new Date().getFullYear()))}
          >
            <option value="">Month</option>
            {MONTHS.map((m, i) => (
              <option key={m} value={m}>
                {MONTH_LABELS[i]}
              </option>
            ))}
          </select>
          <input type="number" aria-label="Start year" placeholder="Year" value={start.year} onChange={(e) => setStart(start.month, e.target.value)} />
        </div>
        <span className="bj-muted" aria-hidden="true">
          {"–"}
        </span>
        <div className="bj-daterange-group">
          <select
            aria-label="End month"
            value={end.month}
            disabled={isCurrent}
            onChange={(e) => setEnd(e.target.value, end.year || String(new Date().getFullYear()))}
          >
            <option value="">Month</option>
            {MONTHS.map((m, i) => (
              <option key={m} value={m}>
                {MONTH_LABELS[i]}
              </option>
            ))}
          </select>
          <input
            type="number"
            aria-label="End year"
            placeholder="Year"
            value={end.year}
            disabled={isCurrent}
            onChange={(e) => setEnd(end.month, e.target.value)}
          />
        </div>
        {currentKey && (
          <label className="bj-current-checkbox">
            <input type="checkbox" checked={isCurrent} onChange={(e) => setCurrent(e.target.checked)} />
            Current
          </label>
        )}
      </div>
    </div>
  );
}
