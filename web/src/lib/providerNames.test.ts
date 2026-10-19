import { describe, expect, it } from "vitest";
import { providerDisplayName } from "./providerNames";
import { providerName } from "./discover";
import { providerLabel as researchLabel } from "./research";
import { providerLabel as hiringLabel } from "./hiringSignals";
import { SEARCH_PROVIDER_CARDS } from "./providerStatus";

describe("provider names", () => {
  it("names a provider, and shows an id it does not know as the server sent it", () => {
    expect(providerDisplayName("brave")).toBe("Brave Search");
    expect(providerDisplayName("newboard")).toBe("newboard");
    expect(providerDisplayName("constructor")).toBe("constructor");
    expect(providerDisplayName("__proto__")).toBe("__proto__");
  });

  it("is the one source every page's wording of a provider comes from", () => {
    for (const id of ["you_com", "firecrawl", "brave", "serper"] as const) {
      expect(providerName(id)).toBe(providerDisplayName(id));
      expect(researchLabel(id)).toBe(providerDisplayName(id));
      expect(hiringLabel(id)).toBe(providerDisplayName(id));
    }
    for (const card of SEARCH_PROVIDER_CARDS) expect(card.title).toBe(providerDisplayName(card.provider));
  });
});
