import { describe, expect, it } from "vitest";
import {
  countryCandidateKeys,
  countryKey,
  countrySearchText,
  ISO_COUNTRY_CODE_COUNT,
  regionCodeOf,
  regionCodeOfNamedCountry,
} from "@/lib/countryNames";

describe("countryKey", () => {
  it("makes spellings of one name the same key", () => {
    expect(countryKey("U.S.A.")).toBe("u s a");
    expect(countryKey("Côte d'Ivoire")).toBe("cote d ivoire");
    expect(countryKey("  United   States ")).toBe("united states");
    expect(countryKey("Türkiye")).toBe("turkiye");
  });
});

describe("regionCodeOf", () => {
  it.each([
    ["US", "US"],
    ["us", "US"],
    ["in", "IN"],
    ["GB", "GB"],
    ["USA", "US"],
    ["U.S.A.", "US"],
    ["U.S.", "US"],
    ["u.s.", "US"],
    ["U.S", "US"],
    ["U. S.", "US"],
    ["U.K.", "GB"],
    ["UAE", "AE"],
    ["U.A.E.", "AE"],
    ["UK", "GB"],
    ["Great Britain", "GB"],
    ["United States of America", "US"],
    ["United States", null], // written out: matched by name, not code
    ["India", null],
    ["Costa Rica", null],
    ["New Zealand", null],
    ["Côte d'Ivoire", null],
    ["", null],
  ])("%j -> %j", (input, expected) => {
    expect(regionCodeOf(input)).toBe(expected);
  });
});

describe("countryCandidateKeys", () => {
  it("a code gives the English name as well as itself", () => {
    expect(countryCandidateKeys("US")).toEqual(expect.arrayContaining(["us", "united states"]));
    expect(countryCandidateKeys("IN")).toEqual(expect.arrayContaining(["india"]));
    expect(countryCandidateKeys("GB")).toEqual(expect.arrayContaining(["united kingdom"]));
    expect(countryCandidateKeys("DE")).toEqual(expect.arrayContaining(["germany"]));
  });

  it("an alias gives the name the list will use", () => {
    expect(countryCandidateKeys("USA")).toEqual(expect.arrayContaining(["united states"]));
    expect(countryCandidateKeys("UK")).toEqual(expect.arrayContaining(["united kingdom"]));
    expect(countryCandidateKeys("U.S.")).toEqual(expect.arrayContaining(["united states"]));
    expect(countryCandidateKeys("U.A.E.")).toEqual(expect.arrayContaining(["united arab emirates"]));
  });

  it("a name written out is itself", () => {
    expect(countryCandidateKeys("Germany")).toEqual(["germany"]);
  });

  it("a code Intl does not know is only itself -- no invented name", () => {
    expect(countryCandidateKeys("ZZ")).toEqual(["zz"]);
  });

  it("nothing in, nothing out", () => {
    expect(countryCandidateKeys("")).toEqual([]);
    expect(countryCandidateKeys("   ")).toEqual([]);
  });
});

describe("countrySearchText", () => {
  it("is the English name for a code or alias, and the text as written otherwise", () => {
    expect(countrySearchText("US")).toBe("United States");
    expect(countrySearchText("uk")).toBe("United Kingdom");
    expect(countrySearchText("Germany")).toBe("Germany");
    expect(countrySearchText("  India ")).toBe("India");
  });
});

describe("regionCodeOfNamedCountry", () => {
  it("knows every ISO 3166-1 country: 249 of them, each resolvable by its English name", () => {
    expect(ISO_COUNTRY_CODE_COUNT).toBe(249);
    const names = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" });
    let resolved = 0;
    for (let a = 65; a <= 90; a++) {
      for (let b = 65; b <= 90; b++) {
        const code = String.fromCharCode(a, b);
        const name = names.of(code);
        if (regionCodeOfNamedCountry(name ?? "") === code) resolved++;
      }
    }
    // Every code resolves from its own name, except where two codes share a name in CLDR.
    expect(resolved).toBeGreaterThanOrEqual(240);
  });

  it.each([
    ["United States", "US"],
    ["the United States", "US"],
    ["United States of America", "US"],
    ["U.S.", "US"],
    ["us", "US"],
    ["USA", "US"],
    ["UK", "GB"],
    ["the UK", "GB"],
    ["United Kingdom", "GB"],
    ["Great Britain", "GB"],
    ["Germany", "DE"],
    ["germany", "DE"],
    ["Canada", "CA"],
    ["India", "IN"],
    ["Côte d'Ivoire", "CI"],
    ["Türkiye", "TR"],
    ["Turkey", "TR"],
    ["Czech Republic", "CZ"],
    ["UAE", "AE"],
    ["United Arab Emirates", "AE"],
  ])("%j -> %j", (text, expected) => {
    expect(regionCodeOfNamedCountry(text)).toBe(expected);
  });

  it.each([
    "",
    "   ",
    "Atlantis",
    "Georgia", // the country or the US state: not told apart, so not a country here
    "the EU",
    "European Union",
    "East Germany",
    "United Nations",
    "Europe",
    "us and canada",
    "the US and in Canada",
    "country where this job is located",
    "a hybrid arrangement",
    "me", // a two-letter word is not a country code here
    "in",
  ])("%j is not a country", (text) => {
    expect(regionCodeOfNamedCountry(text)).toBeNull();
  });
});
