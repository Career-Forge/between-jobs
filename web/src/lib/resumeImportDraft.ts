// The answer to POST /profile/import-document, read defensively.
//
// The server reads a resume file with the person's own AI model and keeps only what the file
// contains (src/between_jobs/api/profile_import_guard.py). What comes back is a DRAFT: a pending
// profile version that nothing uses until the person activates it, with the values that were kept
// (`profile`), where in the extracted text each one was found (`source_spans`), what the model
// proposed that the check did not keep (`dropped`), what was assumed (`assumptions`) and the
// extracted text itself.
//
// This is a boundary, so nothing is trusted to be the right shape: the three things the review
// cannot do without (the draft's id, the profile, and whether it is already the active one) are
// required, and the answer is refused as unreadable without any of them. Everything else has a
// plain default (no warnings, no spans) -- and a span in any unit but UTF-16 is not used at all,
// since highlighting it would mark the wrong words: what the page does not know it says it does
// not know.
//
// Pure module: no React, no network.

import type { CanonicalProfile } from "./profileTypes";
import type { TextSpan } from "./highlightSegments";

export interface DroppedValue {
  // A JSON pointer into the model's draft (`/experience/1/bullets/2`), numbered the way the
  // model listed things, which is not always the way the kept profile is (an entry that was
  // removed above it shifts the rest).
  path: string;
  reason: string;
  detail: string;
  // What the model proposed, shortened by the server; null when it was not text.
  value: string | null;
}

export interface AssumedValue {
  path: string;
  value: string;
  note: string;
}

export interface ImportedDocument {
  kind: string | null;
  filename: string | null;
  pagesTotal: number | null;
  pagesRead: number | null;
  truncated: boolean;
}

export interface ImportDraft {
  versionId: string;
  // This draft IS the profile in use right now (a file whose result matches what is active).
  alreadyActive: boolean;
  profile: CanonicalProfile;
  // Where each kept value was found, keyed by its JSON pointer into `profile`, in UTF-16 units.
  // Empty when the server did not say they are UTF-16.
  spans: Record<string, TextSpan>;
  spansKnown: boolean;
  dropped: DroppedValue[];
  assumptions: AssumedValue[];
  warnings: string[];
  extractedText: string;
  keptFields: number | null;
  droppedFields: number | null;
  document: ImportedDocument;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function countOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function stringsOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function parseSpans(value: unknown): Record<string, TextSpan> {
  const spans: Record<string, TextSpan> = Object.create(null) as Record<string, TextSpan>;
  if (!isRecord(value)) return spans;
  for (const [path, span] of Object.entries(value)) {
    if (!isRecord(span)) continue;
    const { start, end } = span;
    if (typeof start === "number" && typeof end === "number" && Number.isInteger(start) && Number.isInteger(end)) {
      spans[path] = { start, end };
    }
  }
  return spans;
}

function parseDropped(value: unknown): DroppedValue[] {
  if (!Array.isArray(value)) return [];
  const found: DroppedValue[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.path !== "string" || typeof item.reason !== "string") continue;
    found.push({
      path: item.path,
      reason: item.reason,
      detail: typeof item.detail === "string" ? item.detail : "",
      value: textOrNull(item.value),
    });
  }
  return found;
}

function parseAssumptions(value: unknown): AssumedValue[] {
  if (!Array.isArray(value)) return [];
  const found: AssumedValue[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.path !== "string" || typeof item.note !== "string") continue;
    found.push({ path: item.path, value: typeof item.value === "string" ? item.value : "", note: item.note });
  }
  return found;
}

function parseDocument(value: unknown): ImportedDocument {
  const record = isRecord(value) ? value : {};
  return {
    kind: textOrNull(record.kind),
    filename: textOrNull(record.filename),
    pagesTotal: countOrNull(record.pages_total),
    pagesRead: countOrNull(record.pages_read),
    truncated: record.truncated === true,
  };
}

// The draft in an answer, or null when the answer is not one this page can read.
export function parseImportResponse(raw: unknown): ImportDraft | null {
  if (!isRecord(raw)) return null;
  if (typeof raw.version_id !== "string" || raw.version_id === "") return null;
  if (typeof raw.already_active !== "boolean") return null;
  if (!isRecord(raw.profile) || !isRecord(raw.profile.personal)) return null;

  const spansKnown = raw.span_unit === "utf16";
  const stats = isRecord(raw.stats) ? raw.stats : {};
  return {
    versionId: raw.version_id,
    alreadyActive: raw.already_active,
    profile: raw.profile as unknown as CanonicalProfile,
    spans: spansKnown ? parseSpans(raw.source_spans) : parseSpans(null),
    spansKnown,
    dropped: parseDropped(raw.dropped),
    assumptions: parseAssumptions(raw.assumptions),
    warnings: stringsOf(raw.warnings),
    extractedText: typeof raw.extracted_text === "string" ? raw.extracted_text : "",
    keptFields: countOrNull(stats.kept_fields),
    droppedFields: countOrNull(stats.dropped_fields),
    document: parseDocument(raw.document),
  };
}
