import { describe, expect, it } from "vitest";
import providerRegistry from "../../../src/between_jobs/api/provider_registry.py?raw";
import { NOTED_PROVIDERS, mainlandRegionIds, providerNote } from "./providerNotes";

const QWEN =
  "Qwen Cloud runs on Alibaba Cloud. The region you pick is where your requests are received and stored. Where the model runs depends on your workspace's deployment scope, and under the default scope in some regions that can include mainland China.";

function registryProviderIds(): string[] {
  return [...providerRegistry.matchAll(/^    (?:"([a-z]+)"|(OPENROUTER|QWEN|CUSTOM)): ProviderSpec\(/gm)].map((m) => m[1] ?? m[2].toLowerCase());
}

describe("the data-location side-notes", () => {
  it("say, for Qwen Cloud, that the region is where requests are received and stored, and that where the model runs depends on the workspace's scope, which can include mainland China", () => {
    expect(providerNote("qwen")).toBe(QWEN);
    expect(providerNote("qwen", "")).toBe(QWEN);
    expect(providerNote("qwen", "qwencloud")).toBe(QWEN);
  });

  it("say so of the one picked only when the region itself is in mainland China; no other region name rules mainland inference out, and none is described as if it did", () => {
    expect(providerNote("qwen", "cn-beijing")).toBe(`${QWEN} The region you picked is in mainland China.`);
    for (const region of ["cn-hongkong", "ap-southeast-1", "eu-central-1", "ap-northeast-1", "us-east-1", "nowhere"]) {
      expect(providerNote("qwen", region), region).toBe(QWEN);
    }
  });

  it("say, for DeepSeek, who operates it and what its own privacy policy says, and that the policy is silent on API requests, without saying that what you send is stored in China", () => {
    const note = providerNote("deepseek");
    expect(note).toBe(
      "DeepSeek is operated by a company based in China, and its privacy policy says the personal data it collects is processed and stored in the People's Republic of China. That policy does not say whether text sent through the API is covered.",
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
    expect(source).toContain("https://docs.qwencloud.com/developer-guides/getting-started/regions");
    expect(source).toContain("https://www.alibabacloud.com/help/en/model-studio/regions");
  });

  it("does not say the region picked is where the model runs: it is where requests are received and stored, and the deployment scope decides the rest", () => {
    // Hong Kong, Frankfurt and Tokyo default to the Global scope, which can run on mainland nodes, so
    // none of them may be described as keeping what you send out of mainland China.
    for (const region of [null, "cn-hongkong", "eu-central-1", "ap-northeast-1", "us-east-1", "ap-southeast-1"]) {
      const note = providerNote("qwen", region) as string;
      expect(note, String(region)).toContain("received and stored");
      expect(note, String(region)).toContain("deployment scope");
      expect(note, String(region)).toContain("can include mainland China");
      expect(note, String(region)).not.toMatch(/goes to the region|runs in the region|stays in/i);
    }
  });

  it("does not say the text you send is stored in China: DeepSeek's policy does not say it covers API requests, and the note says so", () => {
    const note = providerNote("deepseek") as string;
    expect(note).not.toMatch(/what you send/i);
    expect(note).toContain("does not say whether text sent through the API is covered");
  });
});
