import { useState } from "react";
import type { FieldConfig, SectionFieldConfig } from "../lib/sectionFieldConfigs";

// Generic entry editor -- one modal, driven by a per-section field
// config, used for every "array of typed objects" section (Experience,
// Projects, Education, Publications, Certifications, Languages,
// Volunteering). Built to design-references/profile-edit-versioned-modal
// with two deliberate deviations from that render: no "Location type"
// field (invented in the mockup -- doesn't exist in the schema, so it
// doesn't exist here), and bullet reordering is up/down buttons instead
// of drag-and-drop (no new dependency for this checkpoint).

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
}: {
  config: SectionFieldConfig;
  initialValues: EntryValues;
  isNew: boolean;
  // R5 (resumeforge-shape-and-fit.md): only experience/projects/education
  // reach forge-engines' allocator as pinnable -- the parent decides which
  // sections qualify, this modal just renders (or doesn't) the toggle.
  canPin?: boolean;
  pinnedCountExcludingThis?: number;
  maxPinned?: number;
  versionLabel: string;
  saving: boolean;
  error: string | null;
  onSave: (values: EntryValues) => void;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<EntryValues>(initialValues);

  function setField(key: string, value: unknown) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  const missingRequired = config.fields.some(
    (f) => f.required && !String(values[f.key] ?? "").trim(),
  );
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

  return (
    <div className="bj-modal-backdrop" role="dialog" aria-modal="true">
      <div className="bj-modal">
        <div className="bj-modal-header">
          <h2>
            {isNew ? "Add" : "Edit"} {config.entryLabel.toLowerCase()}
          </h2>
          <button className="bj-modal-close" onClick={onCancel} aria-label="Close">
            ×
          </button>
        </div>
        <div className="bj-modal-body">
          {config.fields.map((field) => (
            <FieldRenderer
              key={field.key}
              field={field}
              values={values}
              onChange={setField}
            />
          ))}
          {canPin && (
            <div className="bj-field bj-pin-field">
              <label className="bj-current-checkbox">
                <input
                  type="checkbox"
                  checked={isPinned}
                  disabled={atPinLimit}
                  onChange={(e) => setMandatory(e.target.checked)}
                />
                📌 Mandatory -- always include this in generated resumes
              </label>
              {isPinned && (
                <label className="bj-field" style={{ marginTop: "0.5rem" }}>
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
                <div className="bj-muted bj-small">
                  You've used all {maxPinned} mandatory slots -- unmark another entry first.
                </div>
              )}
            </div>
          )}
          {error && <div className="bj-error">{error}</div>}
        </div>
        <div className="bj-modal-footer">
          <span className="bj-muted bj-small">{versionLabel}</span>
          <div className="bj-actions">
            <button onClick={onCancel} disabled={saving}>
              Cancel
            </button>
            <button
              className="bj-primary"
              onClick={() => onSave(values)}
              disabled={saving || missingRequired}
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
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
      return (
        <DateRangeField
          field={field}
          values={values}
          onChange={onChange}
        />
      );
    default:
      return null;
  }
}

function BulletsField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string[];
  onChange: (v: string[]) => void;
}) {
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
    <div className="bj-field">
      <span>{label}</span>
      <div className="bj-bullets-editor">
        {value.map((bullet, i) => (
          <div key={i} className="bj-bullet-row">
            <textarea
              rows={2}
              value={bullet}
              onChange={(e) => update(i, e.target.value)}
            />
            <div className="bj-bullet-row-actions">
              <button onClick={() => move(i, -1)} disabled={i === 0} title="Move up">
                ↑
              </button>
              <button onClick={() => move(i, 1)} disabled={i === value.length - 1} title="Move down">
                ↓
              </button>
              <button onClick={() => remove(i)} title="Remove">
                ✕
              </button>
            </div>
          </div>
        ))}
        <button onClick={() => onChange([...value, ""])}>+ Add bullet</button>
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
            <button onClick={() => onChange(value.filter((c) => c !== chip))}>×</button>
          </span>
        ))}
      </div>
      <input
        type="text"
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

const MONTHS = [
  "01",
  "02",
  "03",
  "04",
  "05",
  "06",
  "07",
  "08",
  "09",
  "10",
  "11",
  "12",
];
const MONTH_LABELS = [
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
    <div className="bj-field">
      <span>{field.label}</span>
      <div className="bj-daterange">
        <div className="bj-daterange-group">
          <select value={start.month} onChange={(e) => setStart(e.target.value, start.year || String(new Date().getFullYear()))}>
            <option value="">Month</option>
            {MONTHS.map((m, i) => (
              <option key={m} value={m}>
                {MONTH_LABELS[i]}
              </option>
            ))}
          </select>
          <input
            type="number"
            placeholder="Year"
            value={start.year}
            onChange={(e) => setStart(start.month, e.target.value)}
          />
        </div>
        <span className="bj-muted">–</span>
        <div className="bj-daterange-group">
          <select
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
            placeholder="Year"
            value={end.year}
            disabled={isCurrent}
            onChange={(e) => setEnd(end.month, e.target.value)}
          />
        </div>
        {currentKey && (
          <label className="bj-current-checkbox">
            <input
              type="checkbox"
              checked={isCurrent}
              onChange={(e) => setCurrent(e.target.checked)}
            />
            Current
          </label>
        )}
      </div>
    </div>
  );
}
