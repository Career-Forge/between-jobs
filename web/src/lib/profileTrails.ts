// The trail line under each block of the Profile page: who produced what is shown, on what basis, and
// when -- built only from fields the API returned. A slot whose field was not returned is left out with
// its separator (the omit rule, lib/trail.ts): no model name is ever written here, because no response
// carries one, and no date is written that a field did not give.
//
// Pure module: no React, no clock (the caller passes `now`, which only decides whether a date needs its
// year).

import type { ProfileVersion } from "./profileTypes";
import type { ImportDraft } from "./resumeImportDraft";
import { formatTrailDate, type ReceiptRowInput, type TrailGlyphName, type TrailInput } from "./trail";

export interface BlockTrailSpec {
  trail: TrailInput;
  receipt: ReceiptRowInput[];
  receiptTitle: string;
}

interface SourceWords {
  glyph: TrailGlyphName;
  actor: string;
  basis: string;
}

// How each known `source_kind` produced the version's content. The two file imports are read by the
// person's own model; a JSON import (and every edit, which goes through the same importer) is parsed
// by code; a gap-interview bullet is one the person approved (the model drafts it, a person approves it, and code appends it). A kind the page does not know says
// nothing about who acted.
function sourceWords(sourceKind: string | undefined): SourceWords | null {
  switch (sourceKind) {
    case "web_json_paste":
      return { glyph: "code", actor: "Parsed by code", basis: "no model involved" };
    case "web_pdf_import":
      return { glyph: "model", actor: "Read by your model", basis: "from your PDF file" };
    case "web_docx_import":
      return { glyph: "model", actor: "Read by your model", basis: "from your DOCX file" };
    case "gap_interview":
      return { glyph: "you", actor: "You approved an added bullet", basis: "from the gap interview" };
    default:
      return null;
  }
}

function versionsWord(count: number | undefined): string | null {
  if (typeof count !== "number" || !Number.isInteger(count) || count < 1) return null;
  return count === 1 ? "1 version saved" : `${count} versions saved`;
}

// A short, stable piece of an id for a receipt: enough to tell two apart, never the whole row id.
function idPrefix(id: unknown): string | null {
  return typeof id === "string" && id.trim() !== "" ? id.trim().slice(0, 8) : null;
}

// "Sep 21": when the active version was activated, or null when `activated_at` was not returned or
// cannot be read exactly.
export function activeSince(version: ProfileVersion, now: Date): string | null {
  return formatTrailDate(version.activated_at, { now });
}

// The active profile's content: how the version was made, when it was saved, how many versions the
// person has. The receipt holds the exact fields.
export function activeProfileTrail(version: ProfileVersion, now: Date): BlockTrailSpec {
  const words = sourceWords(version.source_kind);
  return {
    trail: {
      glyph: words?.glyph ?? null,
      actor: words?.actor ?? "Saved to your account",
      basis: words?.basis,
      when: formatTrailDate(version.created_at, { now }),
      extra: versionsWord(version.version_count),
    },
    receipt: [
      { label: "version", value: idPrefix(version.id) },
      { label: "made from", value: version.source_kind },
      { label: "saved", value: version.created_at },
      { label: "activated", value: version.activated_at },
      { label: "versions", value: version.version_count },
      { note: "Every edit you save is a new version; this one is the one in use." },
    ],
    receiptTitle: "Where this profile came from",
  };
}

// The JSON import's preview: parsed by code from the text the person pasted or uploaded.
export function jsonReviewTrail(version: ProfileVersion, now: Date): BlockTrailSpec {
  const stats = version.stats ?? {};
  const counts = (["experience", "projects", "education", "skills"] as const).map(
    (key): ReceiptRowInput => ({ label: key, value: typeof stats[key] === "number" ? stats[key] : null }),
  );
  return {
    trail: {
      glyph: "code",
      actor: "Parsed by code",
      basis: "no model involved",
      when: formatTrailDate(version.created_at, { now }),
      extra: version.warnings !== undefined && version.warnings.length > 0 ? `${version.warnings.length} ${version.warnings.length === 1 ? "warning" : "warnings"}` : null,
    },
    receipt: [
      { label: "draft", value: idPrefix(version.id) },
      { label: "saved", value: version.created_at },
      ...counts,
      { note: "A saved draft is not your profile until you activate it." },
    ],
    receiptTitle: "Where this import came from",
  };
}

// A resume file's draft: a model read the file on the person's own key, so every value may be wrong.
// `fieldCount` is the number of values the person is asked to check.
export function fileReviewTrail(draft: ImportDraft, fileName: string, fieldCount: number): BlockTrailSpec {
  const kind = draft.document.kind === "pdf" || draft.document.kind === "docx" ? draft.document.kind.toUpperCase() : null;
  const pages =
    draft.document.pagesRead !== null && draft.document.pagesTotal !== null
      ? `${draft.document.pagesRead} of ${draft.document.pagesTotal}`
      : null;
  return {
    trail: {
      glyph: "model",
      actor: "Read by your model",
      basis: kind === null ? "from your file, probabilistic" : `from your ${kind} file, probabilistic`,
      extra: `${fieldCount} ${fieldCount === 1 ? "field" : "fields"} to check`,
    },
    receipt: [
      { label: "draft", value: idPrefix(draft.versionId) },
      { label: "file", value: draft.document.filename ?? (fileName === "" ? null : fileName) },
      { label: "type", value: kind },
      { label: "pages read", value: pages },
      { label: "kept", value: draft.keptFields },
      { label: "left out", value: draft.droppedFields },
      { note: "A model read this file, so any value can be wrong. Nothing is used until you confirm it." },
    ],
    receiptTitle: "Where this draft came from",
  };
}

// The header preview's chips are resolved by the engine service from the profile, by code.
export function headerTrail(profileVersionId: string | undefined, chipCount: number): BlockTrailSpec {
  return {
    trail: { glyph: "code", actor: "Chips resolved by code", basis: "from your profile, no model involved" },
    receipt: [
      { label: "profile version", value: idPrefix(profileVersionId) },
      { label: "chips", value: chipCount },
    ],
    receiptTitle: "Where this header came from",
  };
}

// A block whose own read or save failed: the failure's own words, with the red cross.
export function failedBlockTrail(message: string): BlockTrailSpec {
  return { trail: { glyph: "failed", actor: message }, receipt: [], receiptTitle: "What failed" };
}

// The header preview is built from the profile version its resume document was created against, which
// a later activation never moves. True only when both ids are known and differ.
export function isHeaderPreviewStale(documentProfileVersionId: unknown, activeVersionId: unknown): boolean {
  return (
    typeof documentProfileVersionId === "string" &&
    documentProfileVersionId !== "" &&
    typeof activeVersionId === "string" &&
    activeVersionId !== "" &&
    documentProfileVersionId !== activeVersionId
  );
}

export const STALE_HEADER_NOTE = "This preview uses an earlier version of your profile.";

// The compact state the layout shows at 960-1279px, in place of the margin: only what was read.
export function profileSummary(
  version: ProfileVersion,
  now: Date,
  waiting: boolean,
): string[] {
  const since = activeSince(version, now);
  const lines: string[] = [since === null ? "Profile active" : `Profile active since ${since}`];
  const versions = versionsWord(version.version_count);
  if (versions !== null) lines.push(versions);
  if (waiting) lines.push("an import is waiting for review");
  return lines;
}
