import { describe, expect, it } from "vitest";
import providerRegistry from "../../../../src/between_jobs/api/provider_registry.py?raw";
import { parseCredentialRows } from "./credentials";
import {
  MODEL_ID_PATTERN,
  MODEL_ROWS_SHOWN,
  endpointForAddress,
  filterModels,
  formatContext,
  isModelId,
  llmRows,
  llmSetupState,
  parseModelList,
  parseProviderCatalog,
  providerName,
  regionForAddress,
  safeHttpsUrl,
  searchRows,
} from "./llmProviders";
import modelsPy from "../../../../src/between_jobs/api/models.py?raw";

const catalogBody = {
  providers: [
    { id: "openrouter", name: "OpenRouter", key_hint: "Starts with sk-or-.", get_key_url: "https://openrouter.ai/keys", manage_url: "https://openrouter.ai/settings/keys", model_list: "live" },
    {
      id: "qwen",
      name: "Qwen Cloud",
      key_hint: "A QwenCloud key looks like sk-...",
      get_key_url: "https://home.qwencloud.com/api-keys",
      manage_url: "https://home.qwencloud.com/api-keys",
      model_list: "best_effort",
      regions: [
        { id: "qwencloud", name: "QwenCloud", needs_workspace: false, get_key_url: "https://home.qwencloud.com/api-keys" },
        { id: "ap-southeast-1", name: "Alibaba Cloud Model Studio, Singapore", needs_workspace: true, get_key_url: "https://modelstudio.console.alibabacloud.com/ap-southeast-1/model/settings/api-key" },
      ],
    },
    {
      id: "custom",
      name: "Other OpenAI-compatible provider",
      key_hint: "The key your provider gave you.",
      get_key_url: null,
      manage_url: null,
      model_list: "best_effort",
      endpoints: [{ id: "groq", name: "Groq", base_url: "https://api.groq.com/openai/v1", get_key_url: "https://console.groq.com/keys" }],
    },
  ],
};

describe("the provider catalogue", () => {
  it("reads the providers, their regions and the custom endpoints", () => {
    const providers = parseProviderCatalog(catalogBody) ?? [];
    expect(providers.map((p) => p.id)).toEqual(["openrouter", "qwen", "custom"]);
    const qwen = providers[1];
    expect(qwen.listingIsBestEffort).toBe(true);
    expect(qwen.regions.map((r) => [r.id, r.needsWorkspace])).toEqual([["qwencloud", false], ["ap-southeast-1", true]]);
    expect(providers[2].endpoints).toEqual([{ id: "groq", name: "Groq", baseUrl: "https://api.groq.com/openai/v1", getKeyUrl: "https://console.groq.com/keys" }]);
    expect(providers[0].listingIsBestEffort).toBe(false);
  });

  it("is null for an answer that is not a catalogue", () => {
    for (const body of [null, [], "x", {}, { providers: "x" }]) expect(parseProviderCatalog(body)).toBeNull();
  });

  it("leaves out an entry it cannot read, and a provider named twice, rather than guessing", () => {
    const providers = parseProviderCatalog({ providers: [{ id: "a", name: "A" }, { name: "no id" }, 7, { id: "a", name: "A again" }, { id: "b", name: "B" }] });
    expect(providers?.map((p) => p.id)).toEqual(["a", "b"]);
    expect(providers?.[0].name).toBe("A");
  });

  it("turns an address that is not https into no link", () => {
    const providers = parseProviderCatalog({
      providers: [{ id: "x", name: "X", get_key_url: "javascript:alert(1)", manage_url: "http://plain.example/keys" }],
    });
    expect(providers?.[0].getKeyUrl).toBeNull();
    expect(providers?.[0].manageUrl).toBeNull();
  });

  it.each([
    ["https://openrouter.ai/keys", "https://openrouter.ai/keys"],
    ["http://openrouter.ai/keys", null],
    ["javascript:alert(1)", null],
    ["data:text/html,x", null],
    ["https://user:pw@example.com/", null],
    ["//example.com", null],
    ["", null],
    [42, null],
  ])("safeHttpsUrl(%j)", (input, expected) => {
    expect(safeHttpsUrl(input)).toBe(expected);
  });

  it("names every provider the API registry has (the page draws its chips from the answer, so the answer must hold them)", () => {
    const ids = [...providerRegistry.matchAll(/^    (?:"([a-z]+)"|(OPENROUTER|QWEN|CUSTOM)): ProviderSpec\(/gm)].map((m) => m[1] ?? m[2].toLowerCase());
    expect(ids.sort()).toEqual(["anthropic", "custom", "deepseek", "gemini", "openai", "openrouter", "qwen", "xai"]);
  });

  it("falls back to the id for a name the catalogue lacks", () => {
    const providers = parseProviderCatalog(catalogBody);
    expect(providerName("qwen", providers)).toBe("Qwen Cloud");
    expect(providerName("openai", providers)).toBe("openai");
    expect(providerName("openai", null)).toBe("openai");
  });
});

describe("which address a saved key goes to", () => {
  const catalog = parseProviderCatalog(catalogBody) ?? [];
  const custom = catalog.find((provider) => provider.id === "custom");
  const qwen = catalog.find((provider) => provider.id === "qwen");

  it("finds the custom host by its exact address, ignoring a trailing slash, and nothing else", () => {
    const first = custom?.endpoints[0];
    expect(first).toBeDefined();
    expect(endpointForAddress(custom, first?.baseUrl)?.id).toBe(first?.id);
    expect(endpointForAddress(custom, `${first?.baseUrl}/`)?.id).toBe(first?.id);
    expect(endpointForAddress(custom, "https://api.elsewhere.example/v1")).toBeNull();
    expect(endpointForAddress(custom, null)).toBeNull();
    expect(endpointForAddress(custom, "")).toBeNull();
    expect(endpointForAddress(qwen, first?.baseUrl)).toBeNull();
    expect(endpointForAddress(undefined, first?.baseUrl)).toBeNull();
  });

  it("reads Qwen's region from the address the API built, and knows nothing about other hosts", () => {
    expect(regionForAddress(qwen, "https://maas.qwencloudapi.com/compatible-mode/v1")?.id).toBe("qwencloud");
    expect(regionForAddress(qwen, "https://ws-1.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1")?.id).toBe("ap-southeast-1");
    // a region this catalogue does not list, a lookalike host, a non-address and a missing one
    expect(regionForAddress(qwen, "https://ws-1.us-east-1.maas.aliyuncs.com/compatible-mode/v1")).toBeNull();
    expect(regionForAddress(qwen, "https://ws-1.ap-southeast-1.maas.aliyuncs.com.evil.example/v1")).toBeNull();
    expect(regionForAddress(qwen, "not an address")).toBeNull();
    expect(regionForAddress(qwen, null)).toBeNull();
    expect(regionForAddress(custom, "https://maas.qwencloudapi.com/compatible-mode/v1")).toBeNull();
  });
});

describe("a saved key's setup state", () => {
  it.each([
    [{ is_validated: true, model: "m" }, "ready"],
    [{ is_validated: true, model: null }, "needs_model"],
    [{ is_validated: true, model: "" }, "needs_model"],
    [{ is_validated: false, model: "m" }, "needs_key"],
    [{ is_validated: false, model: null }, "needs_key"],
  ] as const)("derives %j as %s the way the server does", (row, expected) => {
    expect(llmSetupState(row)).toBe(expected);
  });

  it("uses the state the server sent when it is one it knows, and ignores one it does not", () => {
    expect(llmSetupState({ is_validated: true, model: "m", setup_state: "needs_model" })).toBe("needs_model");
    expect(llmSetupState({ is_validated: true, model: "m", setup_state: "bogus" })).toBe("ready");
  });

  it("splits model keys from search keys", () => {
    const rows = parseCredentialRows([
      { id: "1", service: "llm", provider: "openai", model: null, is_validated: true, updated_at: "2026-10-01T00:00:00Z", scope: null, setup_state: "needs_model" },
      { id: "2", service: "search", provider: "brave", model: null, is_validated: true, updated_at: "2026-10-01T00:00:00Z", scope: null },
      { id: "3", service: "oauth", provider: "gmail", model: null, is_validated: true, updated_at: "2026-10-01T00:00:00Z", scope: "x" },
    ]);
    expect(llmRows(rows ?? []).map((r) => r.provider)).toEqual(["openai"]);
    expect(searchRows(rows ?? []).map((r) => r.provider)).toEqual(["brave"]);
  });

  it("reads credential rows defensively", () => {
    expect(parseCredentialRows({})).toBeNull();
    expect(parseCredentialRows([null, 3, { service: 1 }, { service: "llm", provider: "openai" }])).toEqual([
      { id: "llm/openai", service: "llm", provider: "openai", model: null, is_validated: false, updated_at: "", scope: null, base_url: null },
    ]);
  });
});

describe("a model list", () => {
  const body = {
    provider: "openai",
    listing: "live",
    truncated: false,
    models: [
      { id: "gpt-5.5", name: "GPT 5.5", context_length: 1_048_576, created: 1_790_000_000 },
      { id: "gpt-5.4-mini", name: "gpt-5.4-mini", context_length: 128_000, created: null },
      { id: "o3", name: "", context_length: null, created: null },
    ],
  };

  it("reads ids, names, context lengths and creation times, in the order given", () => {
    const list = parseModelList(body);
    expect(list?.models.map((m) => m.id)).toEqual(["gpt-5.5", "gpt-5.4-mini", "o3"]);
    expect(list?.models[0]).toEqual({ id: "gpt-5.5", name: "GPT 5.5", contextLength: 1_048_576, created: 1_790_000_000 });
    // a name that only repeats the id is dropped
    expect(list?.models[1].name).toBe("");
    expect(list?.listing).toBe("live");
    expect(list?.truncated).toBe(false);
  });

  it("carries the listing and truncated flags of the server", () => {
    expect(parseModelList({ ...body, listing: "unavailable", models: [] })).toMatchObject({ listing: "unavailable", models: [] });
    expect(parseModelList({ ...body, truncated: true })?.truncated).toBe(true);
  });

  it("is null for something that is not a model list", () => {
    for (const value of [null, [], { provider: "x" }, { provider: "x", models: "y" }, { models: [] }]) expect(parseModelList(value)).toBeNull();
  });

  it("leaves out an id the server would refuse, and a repeat, instead of offering what cannot be chosen", () => {
    const list = parseModelList({
      provider: "x",
      listing: "live",
      truncated: false,
      models: [{ id: "good/model:free" }, { id: "has space" }, { id: "../etc" }, { id: "<script>alert(1)</script>" }, { id: "good/model:free" }, { id: 5 }, null, { id: "a".repeat(201) }],
    });
    expect(list?.models.map((m) => m.id)).toEqual(["good/model:free"]);
    // six entries could not be used (a repeat is not one of them: nothing is lost by it)
    expect(list?.skipped).toBe(6);
  });

  it("counts none as left out for a list that is all usable", () => {
    expect(parseModelList(body)?.skipped).toBe(0);
  });

  it("keeps markup in a provider's name as plain text, cut to a sane length", () => {
    const list = parseModelList({ provider: "x", listing: "live", truncated: false, models: [{ id: "m1", name: `<img src=x onerror=alert(1)>${"y".repeat(500)}` }] });
    expect(list?.models[0].name.length).toBeLessThanOrEqual(120);
    expect(list?.models[0].name.startsWith("<img")).toBe(true);
  });

  it("holds the same rule for an id as the server's model pattern", () => {
    const pattern = /MODEL_ID_PATTERN = r"([^"]+)"/.exec(modelsPy)?.[1];
    expect(pattern).toBe(MODEL_ID_PATTERN.source.replaceAll("\\/", "/"));
    expect(isModelId("anthropic/claude-sonnet-4-6")).toBe(true);
    expect(isModelId("qwen3.8-max")).toBe(true);
    expect(isModelId("")).toBe(false);
    expect(isModelId(" gpt-5")).toBe(false);
  });

  it("filters by every word, in the id or the name, ignoring case", () => {
    const list = parseModelList(body)?.models ?? [];
    expect(filterModels(list, "").length).toBe(3);
    expect(filterModels(list, "GPT mini").map((m) => m.id)).toEqual(["gpt-5.4-mini"]);
    expect(filterModels(list, "5.5").map((m) => m.id)).toEqual(["gpt-5.5"]);
    expect(filterModels(list, "nothing like this")).toEqual([]);
  });

  it("writes a context length in words, and nothing for one not returned", () => {
    expect(formatContext(1_048_576)).toBe("1M tokens");
    expect(formatContext(2_000_000)).toBe("2M tokens");
    expect(formatContext(1_500_000)).toBe("1.5M tokens");
    expect(formatContext(128_000)).toBe("128K tokens");
    expect(formatContext(8192)).toBe("8K tokens");
    expect(formatContext(512)).toBe("512 tokens");
    expect(formatContext(null)).toBeNull();
  });

  it("draws at most a hundred rows of a long list at once", () => {
    expect(MODEL_ROWS_SHOWN).toBe(100);
  });
});
