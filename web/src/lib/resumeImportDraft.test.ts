import { describe, expect, it } from "vitest";
import profileRoutes from "../../../src/between_jobs/api/profile_routes.py?raw";
import { parseImportResponse } from "./resumeImportDraft";

// The answer of POST /profile/import-document, in the shape the server sends it
// (tests/test_profile_import_routes.py pins the same keys on the server's side).
function answer(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const text = "Pat Example\nData Engineer\n";
  return {
    version_id: "11111111-1111-1111-1111-111111111111",
    already_active: false,
    profile: {
      personal: { name: "Pat Example", headline: "Data Engineer" },
      experience: [{ title: "Engineer", company: "Acme", start_date: "2022-01", end_date: "present", is_current: true }],
    },
    span_unit: "utf16",
    source_spans: {
      "/personal/name": { start: 0, end: 11 },
      "/personal/headline": { start: 12, end: 24 },
    },
    dropped: [
      { path: "/skills/tools/2", reason: "not_in_document", detail: "this text is not in the document", value: "HyperWidget" },
    ],
    assumptions: [
      { path: "/experience/0/start_date", value: "2022-01", note: "The document shows only the year." },
    ],
    extracted_text: text,
    stats: { experience: 1, kept_fields: 12, dropped_fields: 1, characters: text.length, llm_attempts: 1 },
    warnings: ["Page 2 was read in columns."],
    document: { kind: "pdf", filename: "resume.pdf", pages_total: 2, pages_read: 2, truncated: false, column_pages: [2] },
    ...overrides,
  };
}

describe("parseImportResponse", () => {
  it("reads a whole answer into a draft", () => {
    const draft = parseImportResponse(answer());
    expect(draft).not.toBeNull();
    expect(draft?.versionId).toBe("11111111-1111-1111-1111-111111111111");
    expect(draft?.alreadyActive).toBe(false);
    expect(draft?.profile.personal.name).toBe("Pat Example");
    expect(draft?.spansKnown).toBe(true);
    expect(draft?.spans["/personal/name"]).toEqual({ start: 0, end: 11 });
    expect(draft?.dropped).toEqual([
      { path: "/skills/tools/2", reason: "not_in_document", detail: "this text is not in the document", value: "HyperWidget" },
    ]);
    expect(draft?.assumptions).toEqual([
      { path: "/experience/0/start_date", value: "2022-01", note: "The document shows only the year." },
    ]);
    expect(draft?.warnings).toEqual(["Page 2 was read in columns."]);
    expect(draft?.extractedText).toBe("Pat Example\nData Engineer\n");
    expect(draft?.keptFields).toBe(12);
    expect(draft?.droppedFields).toBe(1);
    expect(draft?.document).toEqual({
      kind: "pdf",
      filename: "resume.pdf",
      pagesTotal: 2,
      pagesRead: 2,
      truncated: false,
    });
  });

  it("reads already_active as it is", () => {
    expect(parseImportResponse(answer({ already_active: true }))?.alreadyActive).toBe(true);
  });

  it("refuses an answer without the three things a review cannot do without", () => {
    for (const bad of [
      null,
      undefined,
      "draft",
      42,
      [],
      {},
      answer({ version_id: undefined }),
      answer({ version_id: "" }),
      answer({ version_id: 7 }),
      answer({ already_active: undefined }),
      answer({ already_active: "false" }),
      answer({ profile: undefined }),
      answer({ profile: "x" }),
      answer({ profile: [] }),
      answer({ profile: {} }),
      answer({ profile: { personal: null } }),
    ]) {
      expect(parseImportResponse(bad), JSON.stringify(bad)).toBeNull();
    }
  });

  it("falls back to nothing for everything else it is missing, rather than refusing", () => {
    const draft = parseImportResponse({
      version_id: "v",
      already_active: false,
      profile: { personal: { name: "Pat" } },
    });
    expect(draft).toMatchObject({
      spansKnown: false,
      dropped: [],
      assumptions: [],
      warnings: [],
      extractedText: "",
      keptFields: null,
      droppedFields: null,
      document: { kind: null, filename: null, pagesTotal: null, pagesRead: null, truncated: false },
    });
    expect(Object.keys(draft?.spans ?? {})).toEqual([]);
  });

  // Boundary hardening: the server always sends the text as a string; what is not one is taken for
  // no text, never turned into words that were not in the file.
  it("takes extracted text that is not a string for no text, and does not turn it into some", () => {
    for (const value of [42, {}, ["x"], true, null]) {
      expect(parseImportResponse(answer({ extracted_text: value }))?.extractedText, JSON.stringify(value)).toBe("");
    }
  });

  it("does not use spans in a unit it does not know, and says so", () => {
    for (const unit of ["codepoints", "utf8", "", undefined, 16, null]) {
      const draft = parseImportResponse(answer({ span_unit: unit }));
      expect(draft?.spansKnown, String(unit)).toBe(false);
      expect(Object.keys(draft?.spans ?? {}), String(unit)).toEqual([]);
    }
  });

  it("keeps only the spans that are two whole numbers", () => {
    const draft = parseImportResponse(
      answer({
        source_spans: {
          "/a": { start: 0, end: 3 },
          "/b": { start: "0", end: 3 },
          "/c": { start: 1.5, end: 3 },
          "/d": null,
          "/e": [0, 3],
          "/f": { start: 2 },
          "/g": { start: Number.NaN, end: 3 },
        },
      }),
    );
    expect(Object.keys(draft?.spans ?? {})).toEqual(["/a"]);
  });

  it("is safe with a span path that is special to JavaScript", () => {
    const draft = parseImportResponse(
      answer({ source_spans: JSON.parse('{"__proto__": {"start": 0, "end": 1}, "constructor": {"start": 2, "end": 3}}') }),
    );
    expect(draft?.spans.constructor).toEqual({ start: 2, end: 3 });
    expect(draft?.spans["__proto__"]).toEqual({ start: 0, end: 1 });
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("skips dropped values and assumptions that are not the shape it knows, and keeps the rest", () => {
    const draft = parseImportResponse(
      answer({
        dropped: [
          null,
          "x",
          { path: "/a" },
          { reason: "not_in_document" },
          { path: "/ok", reason: "not_in_document" },
          { path: "/ok2", reason: "too_long", detail: 5, value: 5 },
        ],
        assumptions: [{ path: "/a" }, { note: "n" }, { path: "/ok", value: "2020-01", note: "n" }, { path: "/ok2", note: "m" }],
        warnings: ["a", 1, null, "b"],
      }),
    );
    expect(draft?.dropped).toEqual([
      { path: "/ok", reason: "not_in_document", detail: "", value: null },
      { path: "/ok2", reason: "too_long", detail: "", value: null },
    ]);
    expect(draft?.assumptions).toEqual([
      { path: "/ok", value: "2020-01", note: "n" },
      { path: "/ok2", value: "", note: "m" },
    ]);
    expect(draft?.warnings).toEqual(["a", "b"]);
  });

  it("reads page counts only as whole non-negative numbers", () => {
    const draft = parseImportResponse(
      answer({ document: { kind: "docx", filename: null, pages_total: null, pages_read: -1, truncated: "yes" } }),
    );
    expect(draft?.document).toEqual({ kind: "docx", filename: null, pagesTotal: null, pagesRead: null, truncated: false });
  });
});

describe("what the parser reads is what the server sends", () => {
  it("every key it reads is one the route builds", () => {
    for (const key of [
      '"version_id"',
      '"already_active"',
      '"profile"',
      '"span_unit": "utf16"',
      '"source_spans"',
      '"dropped"',
      '"assumptions"',
      '"extracted_text"',
      '"stats"',
      '"kept_fields"',
      '"dropped_fields"',
      '"warnings"',
      '"document"',
      '"pages_total"',
      '"pages_read"',
      '"truncated"',
      '"filename"',
      '"kind"',
      '"path": d.path',
      '"reason": d.reason',
      '"detail": d.detail',
      '"value": d.value',
      '"note": a.note',
    ]) {
      expect(profileRoutes, key).toContain(key);
    }
  });

  it("the route stores a pending version and the spans are in UTF-16 units, as the page assumes", () => {
    expect(profileRoutes).toContain("create_pending_version(");
    expect(profileRoutes).toContain('"span_unit": "utf16"');
    expect(profileRoutes).toContain("to_utf16(span.start)");
  });
});
