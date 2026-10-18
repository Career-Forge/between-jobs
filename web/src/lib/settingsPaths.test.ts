import { describe, expect, it } from "vitest";
import modelProviderSection from "../components/settings/ModelProviderSection.tsx?raw";
import { LEGACY_INTEGRATIONS_PATH, MODEL_PROVIDER_PATH, SETTINGS_PATH } from "./settingsPaths";

// The non-test source of the web app, by path, to check what it names.
const sources = Object.entries(
  import.meta.glob<string>(["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx"], {
    query: "?raw",
    import: "default",
    eager: true,
  }),
);

describe("where Settings lives", () => {
  it("is /settings, with the old Integrations address kept only to redirect", () => {
    expect(SETTINGS_PATH).toBe("/settings");
    expect(LEGACY_INTEGRATIONS_PATH).toBe("/profile/integrations");
  });

  it("aims the model-key link at a section the page really draws", () => {
    const [path, anchor] = MODEL_PROVIDER_PATH.split("#");
    expect(path).toBe(SETTINGS_PATH);
    expect(modelProviderSection).toContain(`id="${anchor}"`);
  });

  it("is the only module that spells either address: no page, row or link types /settings or /profile/integrations itself", () => {
    const spelling = sources
      .filter(([, source]) => /["'`]\/(?:settings|profile\/integrations)(?:[#?"'`])/.test(source))
      .map(([path]) => path.split("/").pop())
      .sort();
    // The route table (the redirect and the page), and the module that names them.
    expect(spelling).toEqual(["App.tsx", "settingsPaths.ts"]);
  });
});
