// @vitest-environment jsdom
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../../testing/fakeApi";
import { choose, pick, settle, text, typeInto } from "../../testing/forms";
import { click, query, render } from "../../testing/render";
import type { CredentialSummary } from "../../lib/settings/credentials";
import { parseProviderCatalog } from "../../lib/settings/llmProviders";
import type { CatalogState } from "../../lib/settings/useProviderCatalog";
import { ModelProviderSection, type ModelProviderSectionProps, type ProviderPanel } from "./ModelProviderSection";

vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);

const NOW = new Date("2026-10-20T12:00:00Z");
const KEY = "sk-" + "z".repeat(32);

const providers = parseProviderCatalog({
  providers: [
    { id: "openrouter", name: "OpenRouter", key_hint: "Starts with sk-or-.", get_key_url: "https://openrouter.ai/keys", manage_url: "https://openrouter.ai/settings/keys", model_list: "live" },
    { id: "openai", name: "OpenAI", key_hint: "Usually starts with sk-.", get_key_url: "https://platform.openai.com/api-keys", manage_url: "https://platform.openai.com/limits", model_list: "live" },
    { id: "anthropic", name: "Anthropic", key_hint: "Starts with sk-ant-.", get_key_url: "https://platform.claude.com/settings/keys", manage_url: null, model_list: "live" },
    {
      id: "custom",
      name: "Other OpenAI-compatible provider",
      key_hint: "The key your provider gave you.",
      get_key_url: null,
      manage_url: null,
      model_list: "best_effort",
      endpoints: [
        { id: "groq", name: "Groq", base_url: "https://api.groq.com/openai/v1", get_key_url: "https://console.groq.com/keys" },
        { id: "mistral", name: "Mistral", base_url: "https://api.mistral.ai/v1", get_key_url: "https://console.mistral.ai/api-keys" },
      ],
    },
    {
      id: "qwen",
      name: "Qwen Cloud",
      key_hint: "A QwenCloud key looks like sk-...",
      get_key_url: "https://home.qwencloud.com/api-keys",
      manage_url: null,
      model_list: "best_effort",
      regions: [
        { id: "qwencloud", name: "QwenCloud", needs_workspace: false, get_key_url: "https://home.qwencloud.com/api-keys" },
        { id: "us-east-1", name: "Alibaba Cloud Model Studio, US (Virginia)", needs_workspace: true, get_key_url: "https://modelstudio.console.alibabacloud.com/us-east-1/model/settings/api-key" },
      ],
    },
  ],
})!;
const READY_CATALOG: CatalogState = { kind: "ready", providers };

function row(provider: string, extra: Partial<CredentialSummary> = {}): CredentialSummary {
  return { id: `id-${provider}`, service: "llm", provider, model: null, is_validated: true, updated_at: "2026-10-18T10:00:00Z", scope: null, ...extra };
}

function Harness(props: Partial<ModelProviderSectionProps> & { credentials: CredentialSummary[] }) {
  const [panel, setPanel] = useState<ProviderPanel>(props.panel ?? null);
  return (
    <ModelProviderSection
      catalog={READY_CATALOG}
      retryCatalog={() => undefined}
      now={NOW}
      openRouter={{ start: () => undefined, busy: false, error: null, unsupported: false }}
      reload={async () => undefined}
      onModelChosen={() => undefined}
      chosenHere={null}
      announce={() => undefined}
      {...props}
      panel={panel}
      onPanel={(next) => {
        props.onPanel?.(next);
        setPanel(next);
      }}
    />
  );
}

function mount(props: Parameters<typeof Harness>[0]) {
  return render(<Harness {...props} />);
}

const button = (root: ParentNode, label: string | RegExp) =>
  [...root.querySelectorAll("button")].find((b) => (typeof label === "string" ? text(b) === label : label.test(text(b)))) as HTMLButtonElement;

beforeEach(() => {
  reset();
});

describe("what is connected", () => {
  it("says a key with a model is validated, shows the model id as text, and when the key was checked", () => {
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5", setup_state: "ready" })] });
    const item = query(container, "#provider-openai");
    expect(text(item)).toContain("OpenAI");
    expect(text(item)).toContain("✓ Validated");
    expect(query(item, ".bj-set-model-line").textContent).toBe("gpt-5.5");
    expect(text(item)).toContain("Checked by code on save");
    expect(text(item)).toContain("Oct 18");
    expect(text(item)).toContain("Billed by OpenAI on your own account.");
    expect(text(item)).toContain("Not yet verified live");
    expect(button(item, "Change model")).toBeDefined();
  });

  it("says a key with no model needs one, offers to choose it, and never shows a default model for it", () => {
    const { container } = mount({ credentials: [row("openai", { setup_state: "needs_model" })] });
    const item = query(container, "#provider-openai");
    expect(text(item)).toContain("Needs a model");
    expect(text(item)).toContain("Your key is saved and checked. Choose a model to use it.");
    expect(text(item)).not.toContain("Validated");
    expect(item.querySelector(".bj-set-model-line")).toBeNull();
    expect(button(item, "Choose a model")).toBeDefined();
    expect(container.querySelector("#default-model")).toBeNull();
  });

  it("says a key that was not confirmed needs a key, offers only to replace it, and gives it no trail", () => {
    const { container } = mount({ credentials: [row("openai", { is_validated: false, model: "gpt-5.5", setup_state: "needs_key" })] });
    const item = query(container, "#provider-openai");
    expect(text(item)).toContain("Needs a key");
    expect(text(item)).toContain("isn't confirmed");
    expect(item.querySelector(".bj-trail")).toBeNull();
    expect(button(item, /Change model|Choose a model/)).toBeUndefined();
    expect(button(item, "Replace key")).toBeDefined();
  });

  it("links to the provider's own page for managing a key, when the catalogue has one", () => {
    const { container } = mount({ credentials: [row("openai", { model: "m" }), row("anthropic", { model: "m" })] });
    const manage = [...container.querySelectorAll("a")].find((a) => a.href === "https://platform.openai.com/limits");
    expect(manage).toBeDefined();
    expect(manage?.rel).toContain("noopener");
    expect(text(query(container, "#provider-anthropic"))).not.toContain("Manage on");
  });

  it("names the host of a custom key, and falls back to the id before the catalogue has loaded", () => {
    const withHost = mount({ credentials: [row("custom", { model: "m", base_url: "https://api.groq.com/openai/v1" })] });
    expect(text(query(withHost.container, "#provider-custom"))).toContain("Host: Groq");
    withHost.unmount();
    const loading = mount({ credentials: [row("openai", { model: "m" })], catalog: { kind: "loading" } });
    expect(text(query(loading.container, "#provider-openai h3"))).toBe("openai");
  });

  it("opens the model picker for the row that was asked for, and closes it again", async () => {
    on("POST /credentials/llm/models", { provider: "openai", listing: "live", truncated: false, models: [{ id: "gpt-5.5", name: "GPT", context_length: null, created: null }] });
    const { container } = mount({ credentials: [row("openai", { setup_state: "needs_model" })] });
    click(button(container, "Choose a model"));
    await settle();
    expect(query(container, "#provider-openai .bj-set-picker")).toBeDefined();
    expect(button(container, "Choose a model").getAttribute("aria-expanded")).toBe("true");
    click(button(container, "Choose a model"));
    expect(container.querySelector(".bj-set-picker")).toBeNull();
  });

  it("opens the picker of a key that was just saved by itself, with its focus on it", async () => {
    on("POST /credentials/llm/models", { provider: "openai", listing: "live", truncated: false, models: [] });
    const { container } = mount({ credentials: [row("openai", { setup_state: "needs_model" })], panel: { provider: "openai", panel: "model" } });
    await settle();
    expect(document.activeElement).toBe(query(container, ".bj-set-picker"));
    expect(text(query(container, ".bj-set-picker h3"))).toBe("Choose a model for OpenAI");
  });

  it("replaces a key with the same form, kept apart from the model", async () => {
    on("POST /credentials", row("openai", { model: "gpt-5.5", setup_state: "ready" }));
    const reload = vi.fn(async () => undefined);
    const announce = vi.fn();
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5", setup_state: "ready" })], reload, announce });
    click(button(container, "Replace key"));
    const form = query<HTMLFormElement>(container, "#provider-openai form");
    expect(text(query(form, "h3"))).toBe("Replace your OpenAI key");
    typeInto(query<HTMLInputElement>(form, 'input[placeholder="Paste your key"]'), KEY);
    click(button(form, "Validate and save"));
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "openai", secret: KEY });
    expect(reload).toHaveBeenCalled();
    expect(announce).toHaveBeenCalledWith("Your OpenAI key was replaced.");
    expect(container.querySelector("form")?.closest("#provider-openai")).toBeNull();
    expect(container.innerHTML).not.toContain(KEY);
  });
});

describe("replacing a key keeps its address", () => {
  it("opens a custom key's replace form on the host it was saved with, not the first one listed", () => {
    const { container } = mount({ credentials: [row("custom", { model: "m", setup_state: "ready", base_url: "https://api.mistral.ai/v1" })] });
    click(button(container, "Replace key"));
    expect(query<HTMLSelectElement>(container, "#provider-custom form select").value).toBe("mistral");
  });

  it("shows the region a Qwen key was saved in, and opens its replace form on it", () => {
    const { container } = mount({
      credentials: [row("qwen", { model: "m", setup_state: "ready", base_url: "https://ws1.us-east-1.maas.aliyuncs.com/compatible-mode/v1" })],
    });
    expect(text(query(container, "#provider-qwen"))).toContain("Region: Alibaba Cloud Model Studio, US (Virginia)");
    click(button(container, "Replace key"));
    expect(query<HTMLSelectElement>(container, "#provider-qwen form select").value).toBe("us-east-1");
  });

  it("says the key stays valid at the host that holds it, not at the umbrella name", () => {
    const { container } = mount({ credentials: [row("custom", { model: "m", base_url: "https://api.mistral.ai/v1" })] });
    click(button(container, "Remove key"));
    expect(text(query(container, ".bj-set-confirm"))).toContain("It stays valid at Mistral until you delete it there.");
  });

  it("goes straight to choosing a model when a replaced key came back without one, and says so", async () => {
    on("POST /credentials", row("qwen", { setup_state: "needs_model", base_url: "https://maas.qwencloudapi.com/compatible-mode/v1" }));
    on("POST /credentials/llm/models", { provider: "qwen", listing: "unavailable", truncated: false, models: [] });
    const onModelDropped = vi.fn();
    const onModelChosen = vi.fn();
    const announce = vi.fn();
    const { container } = mount({
      credentials: [row("qwen", { model: "qwen3-max", setup_state: "ready", base_url: "https://ws1.us-east-1.maas.aliyuncs.com/compatible-mode/v1" })],
      onModelDropped,
      onModelChosen,
      announce,
    });
    click(button(container, "Replace key"));
    choose(query<HTMLSelectElement>(container, "#provider-qwen form select"), "qwencloud");
    expect(text(query(container, "#provider-qwen form"))).toContain("does not carry over");
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="Paste your key"]'), KEY);
    click(button(container, "Validate and save"));
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "qwen", secret: KEY, region: "qwencloud" });
    expect(onModelDropped).toHaveBeenCalledWith("qwen");
    expect(announce).toHaveBeenCalledWith("Your Qwen Cloud key was replaced. Choose a model to finish.");
    expect(container.querySelector("#provider-qwen .bj-set-picker")).not.toBeNull();
    expect(onModelChosen).not.toHaveBeenCalled();
  });
});

describe("keyboard focus after a change", () => {
  it("returns to the row's heading after a model is chosen, instead of falling to the page", async () => {
    on("POST /credentials/llm/models", { provider: "openai", listing: "live", truncated: false, models: [{ id: "gpt-5.5", name: "", context_length: null, created: null }] });
    on("PUT /credentials/llm/openai/model", row("openai", { model: "gpt-5.5", setup_state: "ready" }));
    const { container } = mount({ credentials: [row("openai", { setup_state: "needs_model" })], panel: { provider: "openai", panel: "model" } });
    await settle();
    pick(query<HTMLInputElement>(container, 'input[type="radio"][value="gpt-5.5"]'));
    click(button(container, "Use this model"));
    await settle();
    expect(container.querySelector(".bj-set-picker")).toBeNull();
    expect(document.activeElement).toBe(query(container, "#provider-openai-heading"));
  });

  it("returns to the row's heading after a key is replaced", async () => {
    on("POST /credentials", row("openai", { model: "gpt-5.5", setup_state: "ready" }));
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5", setup_state: "ready" })] });
    click(button(container, "Replace key"));
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="Paste your key"]'), KEY);
    click(button(container, "Validate and save"));
    await settle();
    expect(document.activeElement).toBe(query(container, "#provider-openai-heading"));
  });

  it("returns to the button that opened a panel when the panel is closed without a change", async () => {
    on("POST /credentials/llm/models", { provider: "openai", listing: "live", truncated: false, models: [] });
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5", setup_state: "ready" })], panel: { provider: "openai", panel: "model" } });
    await settle();
    click(button(container, "Close"));
    expect(document.activeElement).toBe(query(container, "#provider-openai-model-button"));
    click(button(container, "Replace key"));
    click(button(container, "Cancel"));
    expect(document.activeElement).toBe(query(container, "#provider-openai-replace-button"));
  });

  it("moves to the section heading when the key it was on is removed", async () => {
    on("DELETE /credentials/llm/openai", undefined);
    const { container } = mount({ credentials: [row("openai", { model: "m" })] });
    click(button(container, "Remove key"));
    click(button(container, "Remove key"));
    await settle();
    expect(document.activeElement).toBe(query(container, "#ai-provider-h"));
  });
});

describe("removing a key", () => {
  it("asks first, says what removing does and does not do, and keeps the key if you decline", () => {
    const { container } = mount({ credentials: [row("openai", { model: "m" })] });
    click(button(container, "Remove key"));
    const group = query(container, '[role="group"][aria-label="Remove the OpenAI key?"]');
    expect(text(group)).toContain("It stays valid at OpenAI until you delete it there.");
    expect(document.activeElement).toBe(button(group, "Remove key"));
    click(button(group, "Keep it"));
    expect(container.querySelector(".bj-set-confirm")).toBeNull();
    expect(document.activeElement).toBe(button(container, "Remove key"));
    expect(callsTo("DELETE /credentials/llm/openai")).toHaveLength(0);
  });

  it("deletes the key by its provider, then re-reads and says so", async () => {
    on("DELETE /credentials/llm/openai", undefined);
    const reload = vi.fn(async () => undefined);
    const announce = vi.fn();
    const { container } = mount({ credentials: [row("openai", { model: "m" })], reload, announce });
    click(button(container, "Remove key"));
    click(button(container, "Remove key")); // the confirmation's own button
    await settle();
    expect(callsTo("DELETE /credentials/llm/openai")).toHaveLength(1);
    expect(reload).toHaveBeenCalled();
    expect(announce).toHaveBeenCalledWith("OpenAI was removed.");
  });

  it("says when removing failed, and keeps the row", async () => {
    on("DELETE /credentials/llm/openai", new TypeError("Failed to fetch"));
    const { container } = mount({ credentials: [row("openai", { model: "m" })] });
    click(button(container, "Remove key"));
    click(button(container, "Remove key"));
    await settle();
    expect(text(query(container, '[role="alert"]'))).toContain("Couldn't reach Between Jobs");
    expect(container.querySelector("#provider-openai")).not.toBeNull();
  });
});

describe("the default model", () => {
  it("is not drawn without a ready key", () => {
    const { container } = mount({ credentials: [] });
    expect(container.querySelector("#default-model")).toBeNull();
  });

  it("is the one ready key's model when there is one", () => {
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5" }), row("anthropic", { setup_state: "needs_model" })] });
    const block = query(container, "#default-model");
    expect(text(block)).toContain("gpt-5.5 on OpenAI.");
    expect(block.querySelector("select")).toBeNull();
  });

  it("claims no default while the stored one may be gone, and lets even a single ready model be made the default", async () => {
    on("PUT /credentials/llm/openai/model", row("openai", { model: "gpt-5.5" }));
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5" }), row("qwen", { setup_state: "needs_model" })], defaultUnknown: true });
    const block = query(container, "#default-model");
    expect(text(block)).not.toContain("gpt-5.5 on OpenAI.");
    expect(text(block)).toContain("can't tell whether it is your default");
    choose(query<HTMLSelectElement>(block, "select"), "openai");
    click(button(block, "Make default"));
    await settle();
    expect(callsTo("PUT /credentials/llm/openai/model")).toHaveLength(1);
  });

  it("does not guess which of several is the default, and changes it by proving the model again", async () => {
    on("PUT /credentials/llm/anthropic/model", row("anthropic", { model: "claude-x" }));
    const onModelChosen = vi.fn();
    const announce = vi.fn();
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5" }), row("anthropic", { model: "claude-x" })], onModelChosen, announce });
    const block = query(container, "#default-model");
    expect(text(block)).toContain("can't tell which one is your default");
    const select = query<HTMLSelectElement>(block, "select");
    expect([...select.options].map((o) => o.textContent)).toEqual(["Choose a connected model", "OpenAI -- gpt-5.5", "Anthropic -- claude-x"]);
    expect(button(block, "Make default").disabled).toBe(true);
    choose(select, "anthropic");
    click(button(block, "Make default"));
    await settle();
    expect(callsTo("PUT /credentials/llm/anthropic/model")[0].body).toEqual({ model: "claude-x" });
    expect(onModelChosen).toHaveBeenCalledWith("anthropic", "claude-x");
    expect(announce).toHaveBeenCalledWith("Anthropic is now your default.");
  });

  it("names the default once it was chosen on this visit, and says why a refusal changed nothing", async () => {
    on("PUT /credentials/llm/openai/model", new FakeApiError("x", "PROVIDER_REJECTED", "model_retired"));
    const { container } = mount({ credentials: [row("openai", { model: "gpt-5.5" }), row("anthropic", { model: "claude-x" })], chosenHere: "anthropic" });
    const block = query(container, "#default-model");
    expect(text(block)).toContain("claude-x on Anthropic.");
    choose(query<HTMLSelectElement>(block, "select"), "openai");
    click(button(block, "Make default"));
    await settle();
    expect(text(query(block, '[role="alert"]'))).toContain("retired that model");
  });
});

describe("adding a provider", () => {
  it("offers only the providers that are not connected yet, OpenRouter first, as a group of chips", () => {
    const { container } = mount({ credentials: [row("anthropic", { model: "m" })] });
    const chips = [...container.querySelectorAll('.bj-set-chips button')];
    expect(chips.map((chip) => text(chip))).toEqual(["OpenRouter", "OpenAI", "Other OpenAI-compatible provider", "Qwen Cloud"]);
    expect(chips.map((chip) => chip.getAttribute("aria-pressed"))).toEqual(["true", "false", "false", "false"]);
    expect(chips.every((chip) => chip.classList.contains("bj-selectable"))).toBe(true);
    expect(query(container, ".bj-segmented").getAttribute("role")).toBe("group");
    expect(text(query(container, ".bj-set-add h3"))).toBe("Add another provider");
  });

  it("shows the form of the chip that is pressed, and a fresh one when it changes", () => {
    const { container } = mount({ credentials: [] });
    expect(text(query(container, ".bj-set-add h3"))).toBe("Connect a provider");
    expect(text(query(container, "form h3"))).toBe("Connect OpenRouter");
    click(button(container, "OpenAI"));
    expect(text(query(container, "form h3"))).toBe("Connect OpenAI");
    expect(button(container, "OpenAI").getAttribute("aria-pressed")).toBe("true");
    expect(button(container, "OpenRouter").getAttribute("aria-pressed")).toBe("false");
  });

  it("saves a key, re-reads, and opens the model picker for it, announcing what to do", async () => {
    on("POST /credentials", row("openai", { setup_state: "needs_model" }));
    const reload = vi.fn(async () => undefined);
    const announce = vi.fn();
    const onPanel = vi.fn();
    const { container } = mount({ credentials: [], reload, announce, onPanel });
    click(button(container, "OpenAI"));
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="Paste your key"]'), KEY);
    click(button(container, "Validate and save"));
    await settle();
    expect(reload).toHaveBeenCalled();
    expect(onPanel).toHaveBeenCalledWith({ provider: "openai", panel: "model" });
    expect(announce).toHaveBeenCalledWith("OpenAI is connected. Choose a model to finish.");
    expect(container.innerHTML).not.toContain(KEY);
  });

  it("does not open the picker for a key that came back with a model already", async () => {
    on("POST /credentials", row("custom", { model: "llama-3", setup_state: "ready" }));
    const onModelChosen = vi.fn();
    const onPanel = vi.fn();
    const announce = vi.fn();
    const { container } = mount({ credentials: [], onModelChosen, onPanel, announce });
    click(button(container, "Other OpenAI-compatible provider"));
    choose(query<HTMLSelectElement>(container, "select"), "groq");
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="Paste your key"]'), KEY);
    click(button(container, "Validate and save"));
    await settle();
    expect(onPanel).toHaveBeenLastCalledWith(null);
    expect(onModelChosen).toHaveBeenCalledWith("custom", "llama-3");
    expect(announce).toHaveBeenCalledWith("Other OpenAI-compatible provider is connected and ready.");
  });

  it("says so, with a way to try again, when the list of providers could not be loaded; the keys still show", () => {
    const retry = vi.fn();
    const { container } = mount({ credentials: [row("openai", { model: "m" })], catalog: { kind: "failed" }, retryCatalog: retry });
    expect(text(query(container, ".bj-set-add"))).toContain("Couldn't load the list of providers, so none can be added yet.");
    expect(container.querySelector("#provider-openai")).not.toBeNull();
    click(button(container, "Try again"));
    expect(retry).toHaveBeenCalled();
  });

  it("shows a placeholder while the list loads, and says when everything is connected", () => {
    const loading = mount({ credentials: [], catalog: { kind: "loading" } });
    expect(loading.container.querySelector('[aria-busy="true"]')).not.toBeNull();
    loading.unmount();
    const all = mount({ credentials: providers.map((p) => row(p.id, { model: "m" })) });
    expect(text(all.container)).toContain("Every provider Between Jobs supports is connected.");
  });
});

describe("the section as a whole", () => {
  it("has no cyan button, and says nothing runs on a shared key", () => {
    const { container } = mount({ credentials: [row("openai", { setup_state: "needs_model" })] });
    expect(container.querySelector(".bj-primary")).toBeNull();
    expect(text(container)).toContain("never runs on a shared key");
    expect(text(container)).toContain("a key it refuses is never stored");
  });

  it("has no serious accessibility violation, empty or with keys in every state", async () => {
    const empty = mount({ credentials: [] });
    await expectNoSeriousA11yViolations(empty.container);
    empty.unmount();
    const full = mount({
      credentials: [row("openai", { model: "gpt-5.5" }), row("anthropic", { setup_state: "needs_model" }), row("custom", { is_validated: false, model: "m", setup_state: "needs_key" })],
    });
    await expectNoSeriousA11yViolations(full.container);
  });
});
