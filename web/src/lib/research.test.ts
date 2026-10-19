import { describe, expect, it } from "vitest";
import {
  contactConfidence,
  countWords,
  failedSearchCount,
  intelConfidence,
  providerLabel,
  providersPhrase,
  runDate,
  safeSourceUrl,
  searchesFailedWords,
  searchesLead,
} from "./research";

const NOW = new Date("2026-09-22T12:00:00Z");

describe("providers", () => {
  it("names the providers a run can report, and shows an id it does not know as the server sent it", () => {
    expect(providerLabel("you_com")).toBe("You.com");
    expect(providerLabel("firecrawl")).toBe("Firecrawl");
    expect(providerLabel("github")).toBe("GitHub");
    expect(providerLabel("serper")).toBe("serper");
    expect(providerLabel("constructor")).toBe("constructor");
    expect(providerLabel("__proto__")).toBe("__proto__");
  });

  it("joins them in words, and is null when none came back", () => {
    expect(providersPhrase(["you_com"])).toBe("You.com");
    expect(providersPhrase(["you_com", "firecrawl"])).toBe("You.com and Firecrawl");
    expect(providersPhrase(["you_com", "firecrawl", "github"])).toBe("You.com, Firecrawl and GitHub");
    expect(providersPhrase([])).toBeNull();
    expect(providersPhrase(["", "  "])).toBeNull();
  });
});

describe("searches that did not complete", () => {
  it("counts one warning as one search, ignoring blank ones", () => {
    expect(failedSearchCount({ warnings: [] })).toBe(0);
    expect(failedSearchCount({ warnings: ["a", "b"] })).toBe(2);
    expect(failedSearchCount({ warnings: ["a", "  ", ""] })).toBe(1);
  });

  it("words the count for the list's lead and for a trail, singular and plural", () => {
    expect(searchesLead(1)).toBe("1 of this run's searches failed or was skipped:");
    expect(searchesLead(3)).toBe("3 of this run's searches failed or were skipped:");
    expect(searchesFailedWords(1)).toBe("1 search failed or skipped");
    expect(searchesFailedWords(2)).toBe("2 searches failed or skipped");
  });
});

describe("dates and counts", () => {
  it("reads the day a run was made from its own field, and says nothing for one it cannot read or for no run", () => {
    expect(runDate({ created_at: "2026-09-20T12:00:00Z" }, NOW, "UTC")).toBe("Sep 20");
    expect(runDate({ created_at: "2025-09-20T12:00:00Z" }, NOW, "UTC")).toBe("Sep 20, 2025");
    expect(runDate({ created_at: "soon" }, NOW)).toBeNull();
    expect(runDate(null, NOW)).toBeNull();
  });

  it("counts in words", () => {
    expect(countWords(1, "claim", "claims")).toBe("1 claim");
    expect(countWords(0, "claim", "claims")).toBe("0 claims");
    expect(countWords(7, "person", "people")).toBe("7 people");
  });
});

describe("confidence", () => {
  it("reads a company intel claim's high, medium and low as 3, 2 and 1 cells of 3", () => {
    expect(["high", "medium", "low"].map((value) => intelConfidence(value))).toEqual([
      { cells: 3, of: 3, word: "High" },
      { cells: 2, of: 3, word: "Medium" },
      { cells: 1, of: 3, word: "Low" },
    ]);
    expect(intelConfidence("")).toBeNull();
    expect(intelConfidence("toString")).toBeNull();
  });

  it("reads a contact source's four values as 4 to 1 cells of 4, in the model's-reading words and never 'verified'", () => {
    const readings = ["verified", "strong", "inferred", "unsupported"].map((value) => contactConfidence(value));
    expect(readings).toEqual([
      { cells: 4, of: 4, word: "Clear match" },
      { cells: 3, of: 4, word: "Likely match" },
      { cells: 2, of: 4, word: "Indirect" },
      { cells: 1, of: 4, word: "Weak" },
    ]);
    expect(readings.map((reading) => reading!.word).join(" ")).not.toMatch(/verified/i);
    expect(contactConfidence("probable")).toBeNull();
  });
});

describe("a source address", () => {
  it("is a link only when it is an http or https address", () => {
    expect(safeSourceUrl("https://example.com/a?b=c#d")).toBe("https://example.com/a?b=c#d");
    expect(safeSourceUrl("  http://example.com  ")).toBe("http://example.com");
  });

  it.each(["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "vbscript:x", "file:///etc/passwd", "ftp://example.com", "//example.com", "/relative", "example.com", "", "   ", null, undefined, 42])("is not a link for %j", (value) => {
    expect(safeSourceUrl(value)).toBeNull();
  });

  it("refuses an address too long to be real", () => {
    expect(safeSourceUrl(`https://example.com/${"a".repeat(2100)}`)).toBeNull();
  });
});
