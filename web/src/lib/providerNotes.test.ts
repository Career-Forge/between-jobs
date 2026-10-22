import { describe, expect, it } from "vitest";
import providerRegistry from "../../../src/between_jobs/api/provider_registry.py?raw";
import { NOTED_PROVIDERS, mainlandRegionIds, providerNote } from "./providerNotes";

const QWEN =
  "Qwen Cloud runs on Alibaba Cloud. What you send goes to the region you pick, and some regions are in mainland China.";

function registryProviderIds(): string[] {
  return [...providerRegistry.matchAll(/^    (?:"([a-z]+)"|(OPENROUTER|QWEN|CUSTOM)): ProviderSpec\(/gm)].map((m) => m[1] ?? m[2].toLowerCase());
}

describe("the data-location side-notes", () => {
  it("say, for Qwen Cloud, that it runs on Alibaba Cloud and that some regions are in mainland China", () => {
    expect(providerNote("qwen")).toBe(QWEN);
    expect(providerNote("qwen", "")).toBe(QWEN);
    expect(providerNote("qwen", "qwencloud")).toBe(QWEN);
  });

  it("say so of the one picked when it is a region in mainland China, and not otherwise", () => {
    expect(providerNote("qwen", "cn-beijing")).toBe(`${QWEN} The one you picked is in mainland China.`);
    for (const region of ["cn-hongkong", "ap-southeast-1", "eu-central-1", "ap-northeast-1", "us-east-1", "nowhere"]) {
      expect(providerNote("qwen", region), region).toBe(QWEN);
    }
  });

  it("say, for DeepSeek, who operates it and what its own privacy policy says about where it processes data", () => {
    const note = providerNote("deepseek");
    expect(note).toBe(
      "DeepSeek is operated by a company based in China. What you send goes to its servers, and its privacy policy says it collects, processes and stores personal data in the People's Republic of China.",
    );
    // a region means nothing to it
    expect(providerNote("deepseek", "cn-beijing")).toBe(note);
  });

  it("are absent for every other provider, including one that is not in the registry (Kimi)", () => {
    for (const id of ["openrouter", "openai", "anthropic", "gemini", "xai", "custom", "moonshot", "kimi", "", "toString", "__proto__"]) {
      expect(providerNote(id), id).toBeNull();
    }
  });

  it("are not warnings: no instruction to stop, no word that says it is unsafe or must be accepted", () => {
    for (const id of NOTED_PROVIDERS) {
      const note = providerNote(id) as string;
      expect(note, id).not.toMatch(/warning|danger|unsafe|risk|do not|don't|must|accept|agree|consent|banned/i);
    }
  });

  it("has an entry only for providers the API registry has, and mainland regions only the registry lists", () => {
    const ids = registryProviderIds();
    for (const id of NOTED_PROVIDERS) expect(ids, id).toContain(id);
    for (const region of mainlandRegionIds("qwen")) expect(providerRegistry, region).toContain(`("${region}",`);
    expect(mainlandRegionIds("deepseek")).toEqual([]);
    expect(mainlandRegionIds("toString")).toEqual([]);
  });

  it("cites the page it rests on, so the wording can be checked again", async () => {
    const source = (await import("./providerNotes.ts?raw")).default;
    expect(source).toContain("https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html");
  });
});
