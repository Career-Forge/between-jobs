import { describe, expect, it } from "vitest";
import { SEARCH_PROVIDER_CARDS } from "../providerStatus";
import { OPTIONAL_EXTRAS_ID, opensOptionalExtras } from "./optionalExtras";

describe("which addresses open the optional extras", () => {
  it("opens for the section's own id, the one the API and other pages link to", () => {
    expect(OPTIONAL_EXTRAS_ID).toBe("search-keys");
    expect(opensOptionalExtras("#search-keys")).toBe(true);
    expect(opensOptionalExtras("search-keys")).toBe(true);
  });

  it("opens for the row of any key the section draws, and for no other", () => {
    for (const card of SEARCH_PROVIDER_CARDS) expect(opensOptionalExtras(`#search-${card.provider}`), card.provider).toBe(true);
    for (const hash of ["", "#", "#ai-provider", "#openrouter", "#saved-searches", "#search-", "#search-nobody", "#search-keys-h", "#SEARCH-KEYS"]) {
      expect(opensOptionalExtras(hash), hash).toBe(false);
    }
  });
});
