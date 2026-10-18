import { describe, expect, it } from "vitest";

// The 429 and 413 wording is only as good as the places that use it: a component that goes
// back to `e instanceof Error ? e.message : ...` shows the server's raw text instead, and
// nothing else (the package has no DOM test setup, and the import stays used by the other
// call sites, so tsc stays quiet) would notice. So this reads the source of every component
// whose action is rate limited and requires each to still route its failures through
// `friendlyApiMessage` -- or through `failureOf`, which calls it after checking for a setup
// failure (lib/setupRequired.ts) -- at least as many times as it did when it was wired up.
//
// vite's `?raw` import, not `node:fs`: @types/node is not installed here.
const sources = import.meta.glob(
  [
    "../components/GeneratePanel.tsx",
    "../components/CompanyIntelPanel.tsx",
    "../components/InterviewPracticePanel.tsx",
    "../pages/Discover.tsx",
    "../components/settings/ProviderConnectCard.tsx",
    "../components/settings/ModelPicker.tsx",
    "../components/settings/ConnectedProviderRow.tsx",
    "../components/settings/DefaultModel.tsx",
    "../components/settings/SearchKeysSection.tsx",
    "../components/settings/ConnectionCards.tsx",
    "../pages/Today.tsx",
  ],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

// Call sites of `friendlyApiMessage(` (or `failureOf(`), per file. A new call site is
// fine; one fewer is a revert.
const MINIMUM_CALL_SITES: Record<string, number> = {
  "../components/GeneratePanel.tsx": 4, // generate, two downloads, the export checklist
  "../components/CompanyIntelPanel.tsx": 1,
  "../components/InterviewPracticePanel.tsx": 2, // start a session, submit an answer
  "../pages/Discover.tsx": 2, // search, track
  // Settings: saving a key (credential_save is behind the enrollment gate), choosing a model, removing
  // one, connecting Gmail. The model-key components word a failure through settingsFailureMessage,
  // which sends a rate limit, an oversized request and a refused enrollment to friendlyApiMessage.
  "../components/settings/ProviderConnectCard.tsx": 1,
  "../components/settings/ModelPicker.tsx": 2, // list the models, choose one
  "../components/settings/ConnectedProviderRow.tsx": 1, // remove a key
  "../components/settings/DefaultModel.tsx": 1,
  "../components/settings/SearchKeysSection.tsx": 2, // save a key, remove one
  "../components/settings/ConnectionCards.tsx": 1, // connect Gmail
  "../pages/Today.tsx": 1, // track
};

describe("components whose actions are rate limited", () => {
  it("are all found (so the counts below are not checking nothing)", () => {
    expect(Object.keys(sources).sort()).toEqual(Object.keys(MINIMUM_CALL_SITES).sort());
  });

  it.each(Object.entries(MINIMUM_CALL_SITES))(
    "%s explains a failure through friendlyApiMessage at least %i time(s)",
    (file, minimum) => {
      const source = sources[file];
      expect(source).toBeTypeOf("string");
      const calls = source.match(/\b(friendlyApiMessage|failureOf|problemOf|settingsFailureMessage)\(/g) ?? [];
      expect(calls.length).toBeGreaterThanOrEqual(minimum);
      expect(source).toMatch(/from "(\.\.\/)+lib\/(rateLimitMessage|setupRequired|settings\/settingsErrors)"/);
    },
  );
});
