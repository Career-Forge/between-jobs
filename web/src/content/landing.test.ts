import { describe, expect, it } from "vitest";
import extensionReadme from "../../../extension/README.md?raw";
import { NOT_VERIFIED_LIVE_DETAIL, PROVIDER_STATUS, providerNotice } from "../lib/providerStatus";
import { LANDING, LANDING_PROVIDER_CAVEAT, LANDING_PROVIDER_IDS, LANDING_PROVIDERS } from "./landing";

// The landing page may not promise more than the product's own pages and notes say. The sentences
// here are tied to their sources, so a change at the source fails here instead of leaving the page
// stale. (pages/Landing.test.tsx checks that they are on the page.)

describe("the model providers the landing page names", () => {
  it("carry the caveat Settings carries: every one but OpenRouter is not yet verified live", () => {
    expect(LANDING_PROVIDER_CAVEAT).not.toBeNull();
    const caveated = LANDING_PROVIDER_IDS.filter((id) => providerNotice(id) !== null);
    expect(caveated).toEqual(LANDING_PROVIDER_IDS.filter((id) => id !== "openrouter"));
    // The sentence names exactly the providers the status table caveats: a provider that is
    // verified there drops out of it, and one that is added to the landing list joins it.
    for (const [index, id] of LANDING_PROVIDER_IDS.entries()) {
      if (caveated.includes(id)) expect(LANDING_PROVIDER_CAVEAT, id).toContain(LANDING_PROVIDERS[index]);
      else expect(LANDING_PROVIDER_CAVEAT, id).not.toContain(LANDING_PROVIDERS[index]);
    }
    // Its claim is the one the Settings card makes, in the same words.
    expect(NOT_VERIFIED_LIVE_DETAIL).toContain("has not had a successful run against the real service yet, so it may fail or return nothing");
  });

  it("are all ids the status table knows", () => {
    for (const id of LANDING_PROVIDER_IDS) expect(Object.keys(PROVIDER_STATUS), id).toContain(id);
    expect(LANDING_PROVIDER_IDS).toHaveLength(LANDING_PROVIDERS.length);
  });
});

describe("what the landing page says about the extension", () => {
  it("does not say it works on live pages while the extension's own notes list that check as still to do", () => {
    expect(extensionReadme).toContain("Real-browser verification that no earlier work could do");
    expect(LANDING.extension.text).not.toMatch(/\bworks on\b/);
    expect(LANDING.extension.text).toContain("It is built for Greenhouse, Lever and Ashby application pages.");
    expect(LANDING.extension.availability).toContain("has not been checked in a real browser against live application pages yet");
  });
});
