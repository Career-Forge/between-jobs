import { describe, expect, it } from "vitest";
import {
  STALE_HEADER_NOTE,
  activeProfileTrail,
  activeSince,
  failedBlockTrail,
  fileReviewTrail,
  headerTrail,
  isHeaderPreviewStale,
  jsonReviewTrail,
  profileSummary,
  type BlockTrailSpec,
} from "./profileTrails";
import type { ProfileVersion } from "./profileTypes";
import { parseImportResponse, type ImportDraft } from "./resumeImportDraft";
import { buildReceipt, buildTrail, findForbiddenWord } from "./trail";

// The trail under each block says what the API returned and nothing else: every slot a field did not
// fill is left out together with its separator.

const NOW = new Date("2026-10-19T12:00:00Z");

function version(overrides: Partial<ProfileVersion> = {}): ProfileVersion {
  return {
    id: "3f2a9c1e-aaaa-bbbb-cccc-1234567890ab",
    canonical_json: { personal: { name: "Asha Verma" } },
    activated_at: "2026-09-21T10:15:00Z",
    created_at: "2026-09-20T11:00:00Z",
    source_kind: "web_json_paste",
    version_count: 4,
    ...overrides,
  };
}

const words = (spec: BlockTrailSpec) => buildTrail(spec.trail)?.text ?? null;
const rows = (spec: BlockTrailSpec) => buildReceipt(spec.receipt)?.rows ?? [];

describe("the active profile's trail", () => {
  it("says a version saved from JSON or an edit was parsed by code, when it was saved and how many versions there are", () => {
    expect(words(activeProfileTrail(version(), NOW))).toBe("Saved from your JSON or edits · parsed by code · Sep 20 · 4 versions saved");
  });

  it("never claims that no model was involved for the kind every edit is stamped with, since an edited PDF read is still a model's reading", () => {
    // The server stamps web_json_paste on every edit, including an edit of a profile a model first read.
    const text = words(activeProfileTrail(version({ source_kind: "web_json_paste", version_count: 6 }), NOW)) ?? "";
    expect(text).not.toMatch(/no model/i);
    expect(buildTrail(activeProfileTrail(version(), NOW).trail)?.glyphs).not.toContain("code");
  });

  it("writes the receipt's made-from in words, and shows a kind it does not know as it came", () => {
    const made = (kind: string) => rows(activeProfileTrail(version({ source_kind: kind }), NOW)).find((row) => "label" in row && row.label === "made from");
    const value = (kind: string) => { const row = made(kind); return row !== undefined && "value" in row ? row.value : undefined; };
    expect(value("web_json_paste")).toBe("pasted or uploaded JSON, or an edit saved from this page");
    expect(value("web_pdf_import")).toBe("a PDF file read by your model");
    expect(value("web_docx_import")).toBe("a DOCX file read by your model");
    expect(value("gap_interview")).toBe("a bullet from the gap interview");
    expect(value("telegram_json")).toBe("telegram_json");
  });

  it("says a resume file was read by the person's model, with the kind of file", () => {
    expect(words(activeProfileTrail(version({ source_kind: "web_pdf_import" }), NOW))).toBe("Read by your model · from your PDF file · Sep 20 · 4 versions saved");
    expect(words(activeProfileTrail(version({ source_kind: "web_docx_import" }), NOW))).toBe("Read by your model · from your DOCX file · Sep 20 · 4 versions saved");
  });

  it("uses the model glyph only for a model's work, the code glyph for code and the you glyph for the person", () => {
    expect(buildTrail(activeProfileTrail(version({ source_kind: "web_pdf_import" }), NOW).trail)?.glyphs).toEqual(["model"]);
    expect(buildTrail(activeProfileTrail(version(), NOW).trail)?.glyphs).toEqual(["you"]);
    expect(buildTrail(activeProfileTrail(version({ source_kind: "gap_interview" }), NOW).trail)?.glyphs).toEqual(["you"]);
  });

  it("never names a model: no response carries one", () => {
    for (const kind of ["web_json_paste", "web_pdf_import", "web_docx_import", "gap_interview", "something_new", undefined]) {
      const text = words(activeProfileTrail(version({ source_kind: kind }), NOW)) ?? "";
      expect(text).not.toMatch(/gpt|claude|gemini|llama|deepseek|openrouter/i);
      expect(findForbiddenWord(text)).toBeNull();
    }
  });

  it("says a gap-interview version holds a bullet the person approved", () => {
    expect(words(activeProfileTrail(version({ source_kind: "gap_interview" }), NOW))).toBe("You approved an added bullet · from the gap interview · Sep 20 · 4 versions saved");
  });

  it("does not guess who acted for a source it does not know", () => {
    const text = words(activeProfileTrail(version({ source_kind: "telegram_json" }), NOW));
    expect(text).toBe("Saved to your account · Sep 20 · 4 versions saved");
    expect(buildTrail(activeProfileTrail(version({ source_kind: "telegram_json" }), NOW).trail)?.glyphs).toEqual([]);
  });

  it("leaves out the date and the count when they were not returned, with their separators", () => {
    const bare = version({ created_at: undefined, version_count: undefined });
    expect(words(activeProfileTrail(bare, NOW))).toBe("Saved from your JSON or edits · parsed by code");
    expect(words(activeProfileTrail(version({ created_at: "not a date" }), NOW))).toBe("Saved from your JSON or edits · parsed by code · 4 versions saved");
  });

  it("says one version in the singular and refuses a count that is not a count", () => {
    expect(words(activeProfileTrail(version({ version_count: 1 }), NOW))).toContain("1 version saved");
    expect(words(activeProfileTrail(version({ version_count: 0 }), NOW))).not.toContain("version saved");
    expect(words(activeProfileTrail(version({ version_count: 2.5 }), NOW))).not.toContain("versions saved");
  });

  it("writes a date from another year with its year", () => {
    expect(words(activeProfileTrail(version({ created_at: "2025-12-31T11:00:00Z" }), NOW))).toContain("Dec 31, 2025");
  });

  it("builds the receipt from returned fields only, with a short id and no other row id", () => {
    const labels = rows(activeProfileTrail(version(), NOW)).map((row) => ("label" in row ? row.label : "note"));
    expect(labels).toEqual(["version", "made from", "saved", "activated", "versions", "note"]);
    const id = rows(activeProfileTrail(version(), NOW)).find((row) => "label" in row && row.label === "version");
    expect(id).toEqual({ label: "version", value: "3f2a9c1e" });
    const sparse = rows(activeProfileTrail({ id: "", canonical_json: { personal: { name: "A" } }, activated_at: null }, NOW)).map((row) => ("label" in row ? row.label : "note"));
    expect(sparse).toEqual(["note"]);
  });
});

describe("activeSince", () => {
  it("is the activation date, or null when it was not returned or cannot be read", () => {
    expect(activeSince(version(), NOW)).toBe("Sep 21");
    expect(activeSince(version({ activated_at: null }), NOW)).toBeNull();
    expect(activeSince(version({ activated_at: "yesterday" }), NOW)).toBeNull();
  });
});

describe("the JSON import review's trail", () => {
  it("is parsed by code, with the date and the warnings counted", () => {
    const text = words(jsonReviewTrail(version({ warnings: ["No education entries."], stats: { experience: 3 } }), NOW));
    expect(text).toBe("Parsed by code · no model involved · Sep 20 · 1 warning");
  });

  it("says nothing of warnings when there are none, and counts only the stats it was given", () => {
    const spec = jsonReviewTrail(version({ warnings: [], stats: { experience: 3, projects: 0 } }), NOW);
    expect(words(spec)).toBe("Parsed by code · no model involved · Sep 20");
    const labels = rows(spec).map((row) => ("label" in row ? `${row.label}=${row.value}` : "note"));
    expect(labels).toEqual(["draft=3f2a9c1e", "saved=2026-09-20T11:00:00Z", "experience=3", "projects=0", "note"]);
  });
});

describe("the resume file review's trail", () => {
  function draft(overrides: Record<string, unknown> = {}): ImportDraft {
    const parsed = parseImportResponse({
      version_id: "11112222-3333-4444-5555-666677778888",
      already_active: false,
      profile: { personal: { name: "Pat Example" } },
      span_unit: "utf16",
      source_spans: {},
      dropped: [],
      assumptions: [],
      extracted_text: "Pat Example",
      stats: { kept_fields: 26, dropped_fields: 4 },
      warnings: [],
      document: { kind: "pdf", filename: "cv.pdf", pages_total: 2, pages_read: 2, truncated: false },
      ...overrides,
    });
    if (parsed === null) throw new Error("the fixture is not a draft");
    return parsed;
  }

  it("says a model read the file and that every value may be wrong, with the number to check", () => {
    expect(words(fileReviewTrail(draft(), "cv.pdf", 26))).toBe("Read by your model · from your PDF file, probabilistic · 26 fields to check");
    expect(buildTrail(fileReviewTrail(draft(), "cv.pdf", 26).trail)?.glyphs).toEqual(["model"]);
  });

  it("does not say what kind of file it was when the server did not", () => {
    expect(words(fileReviewTrail(draft({ document: {} }), "", 1))).toBe("Read by your model · from your file, probabilistic · 1 field to check");
  });

  it("has no date slot: the draft carries no time, and none is made up", () => {
    expect(words(fileReviewTrail(draft(), "cv.pdf", 26))).not.toMatch(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b|\d\d:\d\d/);
  });

  it("builds the receipt from the draft's own fields", () => {
    const labels = rows(fileReviewTrail(draft(), "cv.pdf", 26)).map((row) => ("label" in row ? `${row.label}=${row.value}` : "note"));
    expect(labels).toEqual(["draft=11112222", "file=cv.pdf", "type=PDF", "pages read=2 of 2", "kept=26", "left out=4", "note"]);
  });

  it("leaves out the counts the server did not give", () => {
    const labels = rows(fileReviewTrail(draft({ stats: {} }), "", 0)).map((row) => ("label" in row ? row.label : "note"));
    expect(labels).not.toContain("kept");
    expect(labels).not.toContain("left out");
  });
});

describe("the header block's trail and its stale note", () => {
  it("says the chips were resolved by code, with no date (none is returned)", () => {
    expect(words(headerTrail("aaaaaaaa-1111", 4))).toBe("Chips resolved by code · from your profile, no model involved");
    expect(rows(headerTrail(undefined, 0))).toEqual([{ label: "chips", value: "0" }]);
  });

  it("is stale only when both versions are known and differ", () => {
    expect(isHeaderPreviewStale("v1", "v2")).toBe(true);
    expect(isHeaderPreviewStale("v1", "v1")).toBe(false);
    expect(isHeaderPreviewStale(undefined, "v2")).toBe(false);
    expect(isHeaderPreviewStale("v1", "")).toBe(false);
    expect(isHeaderPreviewStale(null, null)).toBe(false);
    expect(STALE_HEADER_NOTE).toBe("This preview uses an earlier version of your profile.");
  });

  it("draws a failed block with the red cross and the failure's own words", () => {
    const spec = failedBlockTrail("Failed to load");
    expect(words(spec)).toBe("Failed to load");
    expect(buildTrail(spec.trail)?.glyphs).toEqual(["failed"]);
    expect(buildReceipt(spec.receipt)).toBeNull();
  });
});

describe("the compact summary for 960-1279px", () => {
  it("lists only what was read", () => {
    expect(profileSummary(version(), NOW, false)).toEqual(["Profile active since Sep 21", "4 versions saved"]);
    expect(profileSummary(version(), NOW, true)).toEqual(["Profile active since Sep 21", "4 versions saved", "an import is waiting for review"]);
    expect(profileSummary(version({ activated_at: null, version_count: undefined }), NOW, false)).toEqual(["Profile active"]);
  });
});
