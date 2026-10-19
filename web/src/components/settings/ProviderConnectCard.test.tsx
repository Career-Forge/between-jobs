// @vitest-environment jsdom
import { act } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { FakeApiError, calls, callsTo, on, reset } from "../../testing/fakeApi";
import { choose, settle, submit, text, typeInto } from "../../testing/forms";
import { click, query, render } from "../../testing/render";
import { parseProviderCatalog, type LlmProvider } from "../../lib/settings/llmProviders";
import { ProviderConnectCard, type ProviderConnectCardProps } from "./ProviderConnectCard";

vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);

// A key-shaped test value, built at run time (a literal would look like a real key to a scanner).
const KEY = "sk-" + "t".repeat(32);

const catalog = parseProviderCatalog({
  providers: [
    { id: "openrouter", name: "OpenRouter", key_hint: "Starts with sk-or-.", get_key_url: "https://openrouter.ai/keys", manage_url: "https://openrouter.ai/settings/keys", model_list: "live" },
    { id: "openai", name: "OpenAI", key_hint: "Usually starts with sk-.", get_key_url: "https://platform.openai.com/settings/organization/api-keys", manage_url: null, model_list: "live" },
    {
      id: "qwen",
      name: "Qwen Cloud",
      key_hint: "A QwenCloud key looks like sk-...",
      get_key_url: "https://home.qwencloud.com/api-keys",
      manage_url: null,
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
      endpoints: [
        { id: "groq", name: "Groq", base_url: "https://api.groq.com/openai/v1", get_key_url: "https://console.groq.com/keys" },
        { id: "mistral", name: "Mistral", base_url: "https://api.mistral.ai/v1", get_key_url: "https://console.mistral.ai/api-keys" },
      ],
    },
  ],
}) as LlmProvider[];

function provider(id: string): LlmProvider {
  const found = catalog.find((candidate) => candidate.id === id);
  if (found === undefined) throw new Error(id);
  return found;
}

const SAVED_ROW = { id: "c1", service: "llm", provider: "openai", model: null, is_validated: true, updated_at: "2026-10-18T10:00:00Z", scope: null, setup_state: "needs_model" };

function mount(overrides: Partial<ProviderConnectCardProps> & { provider: LlmProvider }) {
  const onSaved = vi.fn();
  const rendered = render(<ProviderConnectCard mode="add" onSaved={onSaved} {...overrides} />);
  const form = query<HTMLFormElement>(rendered.container, "form");
  return { ...rendered, form, onSaved };
}

function keyInput(root: ParentNode): HTMLInputElement {
  return query<HTMLInputElement>(root, 'input[autocomplete="off"][placeholder="Paste your key"]');
}

beforeEach(() => {
  reset();
  vi.restoreAllMocks();
});

describe("a plain key provider", () => {
  it("links to where the provider issues a key, and says who bills and that it has not been seen working", () => {
    const { container } = mount({ provider: provider("openai") });
    const link = query<HTMLAnchorElement>(container, "a");
    expect(link.href).toBe("https://platform.openai.com/settings/organization/api-keys");
    expect(link.target).toBe("_blank");
    expect(link.rel).toContain("noopener");
    expect(text(container)).toContain("Billed by OpenAI on your own account.");
    expect(text(container)).toContain("Not yet verified live");
    expect(text(container)).toContain("Usually starts with sk-.");
  });

  it("masks the key, and shows or hides it on request", () => {
    const { container } = mount({ provider: provider("openai") });
    const input = keyInput(container);
    expect(input.type).toBe("password");
    expect(input.autocomplete).toBe("off");
    expect(input.getAttribute("spellcheck")).toBe("false");
    click(query(container, "button.bj-set-reveal"));
    expect(keyInput(container).type).toBe("text");
    expect(text(query(container, "button.bj-set-reveal"))).toBe("Hide");
    click(query(container, "button.bj-set-reveal"));
    expect(keyInput(container).type).toBe("password");
  });

  it("sends the key to the API and nowhere else, then empties the field and shows the key nowhere", async () => {
    on("POST /credentials", SAVED_ROW);
    const { container, form, onSaved } = mount({ provider: provider("openai") });
    typeInto(keyInput(container), `  ${KEY}  `);
    click(query(container, "button.bj-set-reveal")); // shown, to prove a shown key is cleared too
    submit(form);
    await settle();

    expect(callsTo("POST /credentials")).toHaveLength(1);
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "openai", secret: KEY });
    // After the save: the field is empty and masked again, and the key is in no text and no attribute.
    expect(keyInput(container).value).toBe("");
    expect(keyInput(container).type).toBe("password");
    expect(container.innerHTML).not.toContain(KEY);
    expect(document.body.innerHTML).not.toContain(KEY);
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ provider: "openai", setup_state: "needs_model" }));
  });

  it("does not send a request for an empty field, and says what is missing", async () => {
    const { container, form } = mount({ provider: provider("openai") });
    submit(form);
    await settle();
    expect(calls).toHaveLength(0);
    expect(text(query(container, '[role="alert"]'))).toBe("Paste your OpenAI key first.");
  });

  it("shows Validating while the request is out, and does not send a second one for a second press", async () => {
    let finish: (value: unknown) => void = () => undefined;
    on("POST /credentials", () => new Promise((resolve) => (finish = resolve)));
    const { container, form } = mount({ provider: provider("openai") });
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle(1);
    expect(text(query(container, 'button[type="submit"]'))).toBe("Validating...");
    expect(query(container, 'button[type="submit"]').getAttribute("aria-disabled")).toBe("true");
    expect(text(query(container, '[role="status"]'))).toContain("Checking your key with OpenAI");
    submit(form);
    await settle(1);
    expect(callsTo("POST /credentials")).toHaveLength(1);
    finish(SAVED_ROW);
    await settle();
  });

  // Enter in the field is what starts the save, so the field must not disable itself: a disabled field drops
  // keyboard focus to the top of the page, and a refused key would leave it there.
  it("keeps keyboard focus in the key field while it validates, and after a refusal", async () => {
    let refuse: (error: unknown) => void = () => undefined;
    on("POST /credentials", () => new Promise((_resolve, reject) => (refuse = reject)));
    const { container, form } = mount({ provider: provider("openai") });
    const field = keyInput(container);
    typeInto(field, KEY);
    field.focus();
    submit(form);
    await settle(1);
    expect(field.readOnly).toBe(true);
    expect(field.disabled).toBe(false);
    expect(document.activeElement).toBe(field);
    await act(async () => refuse(new FakeApiError("OpenAI rejected that key.", "PROVIDER_REJECTED", "key_rejected")));
    await settle();
    expect(field.readOnly).toBe(false);
    expect(document.activeElement).toBe(field);
  });

  it("says why a key was refused, in the provider's name, keeps what was typed, and leaks nothing", async () => {
    on("POST /credentials", new FakeApiError("OpenAI rejected that key.", "PROVIDER_REJECTED", "key_rejected"));
    const { container, form, onSaved } = mount({ provider: provider("openai") });
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    const alert = text(query(container, '[role="alert"]'));
    expect(alert).toContain("OpenAI didn't accept that key");
    expect(alert).toContain("Nothing was saved.");
    expect(alert).not.toContain(KEY);
    expect(onSaved).not.toHaveBeenCalled();
    expect(keyInput(container).value).toBe(KEY);
  });

  it("says it could not reach the service when the request never got there", async () => {
    on("POST /credentials", new TypeError("Failed to fetch"));
    const { container, form } = mount({ provider: provider("openai") });
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(text(query(container, '[role="alert"]'))).toBe("Couldn't reach Between Jobs. Check your connection and try again.");
  });

  it("is titled for replacing when it replaces, and offers Cancel", () => {
    const onCancel = vi.fn();
    const { container } = mount({ provider: provider("openai"), mode: "replace", onCancel });
    expect(text(query(container, "h3"))).toBe("Replace your OpenAI key");
    click(query(container, "button.bj-quiet"));
    expect(onCancel).toHaveBeenCalled();
  });

  it("does not repeat who bills and the not-verified notice when replacing: the row above it already says so", () => {
    const adding = mount({ provider: provider("openai") });
    expect(text(adding.container)).toContain("Not yet verified live");
    adding.unmount();
    const replacing = mount({ provider: provider("openai"), mode: "replace" });
    expect(text(replacing.container)).not.toContain("Not yet verified live");
    expect(text(replacing.container)).not.toContain("Billed by OpenAI");
  });

  it("has no cyan button: the page's one primary action is the next-step band's", () => {
    const { container } = mount({ provider: provider("openai") });
    expect(container.querySelector(".bj-primary")).toBeNull();
    expect(container.querySelector(".bj-button-link-primary")).toBeNull();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = mount({ provider: provider("openai") });
    await expectNoSeriousA11yViolations(container);
  });
});

describe("OpenRouter", () => {
  it("offers the one-click connection first, explains it, and the paste path after it", () => {
    const start = vi.fn();
    const { container } = mount({ provider: provider("openrouter"), openRouter: { start, busy: false, error: null, unsupported: false } });
    const connect = [...container.querySelectorAll("button")].find((b) => text(b) === "Connect with OpenRouter");
    expect(connect).toBeDefined();
    expect(text(container)).toContain("You sign in at OpenRouter, press Allow, and come straight back. No key to copy.");
    expect(text(container)).toContain("or paste a key");
    click(connect as HTMLButtonElement);
    expect(start).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".bj-primary")).toBeNull();
  });

  it("says OpenRouter adds a fee when credits are bought, with a link to its pricing and no number", () => {
    const { container } = mount({ provider: provider("openrouter") });
    expect(text(container)).toContain("OpenRouter adds a fee when you buy credits");
    const pricing = [...container.querySelectorAll("a")].find((a) => a.href === "https://openrouter.ai/pricing");
    expect(pricing).toBeDefined();
    expect(text(container)).not.toMatch(/\d+(\.\d+)?\s?%/);
    // OpenRouter makes no claim either way about being verified live.
    expect(text(container)).not.toContain("Not yet verified live");
  });

  it("does not start a second sign-in while one is starting, and shows a start failure", () => {
    const start = vi.fn();
    const { container } = mount({ provider: provider("openrouter"), openRouter: { start, busy: true, error: "Nope.", unsupported: false } });
    const connect = [...container.querySelectorAll("button")].find((b) => text(b) === "Opening OpenRouter...") as HTMLButtonElement;
    click(connect);
    expect(start).not.toHaveBeenCalled();
    expect(text(query(container, '[role="alert"]'))).toBe("Nope.");
  });

  it("leaves out the one-click button where the browser cannot run it, and keeps the paste path", () => {
    const { container } = mount({ provider: provider("openrouter"), openRouter: { start: vi.fn(), busy: false, error: null, unsupported: true } });
    expect([...container.querySelectorAll("button")].some((b) => text(b) === "Connect with OpenRouter")).toBe(false);
    expect(keyInput(container)).toBeDefined();
  });
});

describe("Qwen", () => {
  it("asks for a region from the catalogue, and a workspace id only for a Model Studio region", () => {
    const { container } = mount({ provider: provider("qwen") });
    const region = query<HTMLSelectElement>(container, "select");
    expect([...region.options].map((o) => o.textContent)).toEqual(["QwenCloud", "Alibaba Cloud Model Studio, Singapore"]);
    expect(container.querySelector('input[placeholder="From your Model Studio console"]')).toBeNull();
    choose(region, "ap-southeast-1");
    expect(container.querySelector('input[placeholder="From your Model Studio console"]')).not.toBeNull();
    expect(text(container)).toContain("A key only works in the region it was created in.");
    // the key link follows the region
    expect(query<HTMLAnchorElement>(container, "a").href).toContain("modelstudio.console.alibabacloud.com/ap-southeast-1");
  });

  it("sends the QwenCloud region without a workspace id", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "qwen" });
    const { container, form } = mount({ provider: provider("qwen") });
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "qwen", secret: KEY, region: "qwencloud" });
  });

  it("will not send a Model Studio key without its workspace id, then sends both", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "qwen" });
    const { container, form } = mount({ provider: provider("qwen") });
    choose(query<HTMLSelectElement>(container, "select"), "ap-southeast-1");
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(calls).toHaveLength(0);
    expect(text(query(container, '[role="alert"]'))).toBe("This region needs your Model Studio workspace id.");
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="From your Model Studio console"]'), " ws-abc-123 ");
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "qwen", secret: KEY, region: "ap-southeast-1", workspace_id: "ws-abc-123" });
  });
});

describe("the custom provider", () => {
  it("shows a Base URL chosen from the short list, says the list is the limit, and never offers free text", () => {
    const { container } = mount({ provider: provider("custom") });
    const select = query<HTMLSelectElement>(container, "select");
    expect(query(container, "label[for]").textContent).toBe("Base URL");
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "Choose the host your key is from",
      "Groq -- https://api.groq.com/openai/v1",
      "Mistral -- https://api.mistral.ai/v1",
    ]);
    expect(text(container)).toContain("Only these OpenAI-compatible hosts are accepted (Groq, Mistral).");
    expect(text(container)).toContain("Your key is sent to the one you pick and nowhere else.");
    expect(container.querySelector('input[type="url"]')).toBeNull();
    // nothing is picked for the person, so there is no key link until one is
    expect(select.value).toBe("");
    expect(container.querySelector("a")).toBeNull();
    choose(select, "groq");
    expect(query<HTMLAnchorElement>(container, "a").href).toBe("https://console.groq.com/keys");
    expect(text(query<HTMLAnchorElement>(container, "a"))).toContain("Get your key at Groq");
    choose(select, "mistral");
    expect(query<HTMLAnchorElement>(container, "a").href).toBe("https://console.mistral.ai/api-keys");
    expect(text(query<HTMLAnchorElement>(container, "a"))).toContain("Get your key at Mistral");
  });

  it("sends the address of the host that was picked", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "custom" });
    const { container, form } = mount({ provider: provider("custom") });
    choose(query<HTMLSelectElement>(container, "select"), "mistral");
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "custom", secret: KEY, base_url: "https://api.mistral.ai/v1" });
  });
});

describe("where a key is sent is never a default", () => {
  it("will not send a custom-provider key until a host is picked, and says why", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "custom" });
    const { container, form } = mount({ provider: provider("custom") });
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(calls).toHaveLength(0);
    expect(text(query(container, '[role="alert"]'))).toBe("Choose which provider's address to use.");
    expect(keyInput(container).value).toBe(KEY);
    // and the words around the form do not name a company the person did not choose
    expect(container.querySelector("a")).toBeNull();
    expect(text(container)).toContain("checks the key with the host you choose first");
  });

  it("names the host that was picked in the status and the footer, not the umbrella name", async () => {
    let release: (value: unknown) => void = () => undefined;
    on("POST /credentials", () => new Promise((resolve) => (release = resolve)));
    const { container, form } = mount({ provider: provider("custom") });
    choose(query<HTMLSelectElement>(container, "select"), "mistral");
    expect(text(container)).toContain("checks the key with Mistral first");
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(text(query(container, '[role="status"]'))).toBe("Checking your key with Mistral...");
    release({ ...SAVED_ROW, provider: "custom" });
    await settle();
  });

  it("replaces a custom key at the host it was saved with, and sends that host", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "custom", model: "m", setup_state: "ready" });
    const { container, form } = mount({ provider: provider("custom"), mode: "replace", savedBaseUrl: "https://api.mistral.ai/v1/" });
    expect(query<HTMLSelectElement>(container, "select").value).toBe("mistral");
    expect(text(query<HTMLAnchorElement>(container, "a"))).toContain("Get your key at Mistral");
    expect(container.querySelector(".bj-set-notice")).toBeNull();
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "custom", secret: KEY, base_url: "https://api.mistral.ai/v1" });
  });

  it("warns that another host starts the model over, when a replacement is pointed somewhere else", () => {
    const { container } = mount({ provider: provider("custom"), mode: "replace", savedBaseUrl: "https://api.mistral.ai/v1" });
    choose(query<HTMLSelectElement>(container, "select"), "groq");
    expect(text(query(container, ".bj-set-notice"))).toContain("the model it had does not carry over");
  });

  it("asks for a choice, and sends nothing, when the saved host is none of the listed ones", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "custom" });
    const { container, form } = mount({ provider: provider("custom"), mode: "replace", savedBaseUrl: "https://api.elsewhere.example/v1" });
    expect(query<HTMLSelectElement>(container, "select").value).toBe("");
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(calls).toHaveLength(0);
    expect(text(query(container, '[role="alert"]'))).toBe("Choose which provider's address to use.");
  });

  it("replaces a Model Studio Qwen key in the region it was saved in, which needs its workspace id again", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "qwen", model: "m", setup_state: "ready" });
    const { container, form } = mount({
      provider: provider("qwen"),
      mode: "replace",
      savedBaseUrl: "https://ws-abc-123.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    });
    expect(query<HTMLSelectElement>(container, "select").value).toBe("ap-southeast-1");
    expect(container.querySelector('input[placeholder="From your Model Studio console"]')).not.toBeNull();
    expect(container.querySelector(".bj-set-notice")).toBeNull();
    typeInto(keyInput(container), KEY);
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="From your Model Studio console"]'), "ws-abc-123");
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "qwen", secret: KEY, region: "ap-southeast-1", workspace_id: "ws-abc-123" });
  });

  it("replaces a QwenCloud key on QwenCloud, and asks for a region when the saved address is not known", async () => {
    on("POST /credentials", { ...SAVED_ROW, provider: "qwen" });
    const known = mount({ provider: provider("qwen"), mode: "replace", savedBaseUrl: "https://maas.qwencloudapi.com/compatible-mode/v1" });
    expect(query<HTMLSelectElement>(known.container, "select").value).toBe("qwencloud");
    known.unmount();
    const { container, form } = mount({ provider: provider("qwen"), mode: "replace", savedBaseUrl: null });
    expect(query<HTMLSelectElement>(container, "select").value).toBe("");
    expect(container.querySelector("a")).toBeNull();
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(calls).toHaveLength(0);
    expect(text(query(container, '[role="alert"]'))).toBe("Choose the region your key came from.");
  });
});

describe("a provider that has no free way to check a key", () => {
  it("asks for a model id only after the API says so, keeps the key, and sends the model with it", async () => {
    on(
      "POST /credentials",
      new FakeApiError("Other has no free way to check a key.", "INVALID_INPUT", "model_required_to_check_key"),
    );
    const { container, form, onSaved } = mount({ provider: provider("custom") });
    expect(container.querySelector('input[placeholder="provider/model-name"]')).toBeNull();
    choose(query<HTMLSelectElement>(container, "select"), "groq");
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(text(query(container, '[role="alert"]'))).toContain("needs a model id too");
    const model = query<HTMLInputElement>(container, 'input[placeholder="provider/model-name"]');
    expect(document.activeElement).toBe(model);
    expect(keyInput(container).value).toBe(KEY);

    on("POST /credentials", { ...SAVED_ROW, provider: "custom", model: "llama-3", setup_state: "ready" });
    typeInto(model, "llama-3");
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")[1].body).toEqual({ service: "llm", provider: "custom", secret: KEY, base_url: "https://api.groq.com/openai/v1", model: "llama-3" });
    expect(onSaved).toHaveBeenCalled();
    expect(keyInput(container).value).toBe("");
  });

  it("refuses a model id that cannot be one before sending anything", async () => {
    on("POST /credentials", new FakeApiError("x", "INVALID_INPUT", "model_required_to_check_key"));
    const { container, form } = mount({ provider: provider("custom") });
    choose(query<HTMLSelectElement>(container, "select"), "groq");
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="provider/model-name"]'), "not a model id");
    submit(form);
    await settle();
    expect(callsTo("POST /credentials")).toHaveLength(1);
    expect(text(query(container, '[role="alert"]'))).toContain("doesn't look like a model id");
  });
});

describe("secrets", () => {
  it("writes the key to neither local storage, session storage nor the console", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) => vi.spyOn(console, level).mockImplementation(() => undefined));
    on("POST /credentials", SAVED_ROW);
    // Storage stand-ins that record every write, whatever the environment offers.
    const written: string[] = [];
    const recorder = { setItem: (k: string, v: string) => void written.push(`${k}=${v}`), getItem: () => null, removeItem: () => undefined };
    vi.stubGlobal("localStorage", recorder);
    vi.stubGlobal("sessionStorage", recorder);
    const { container, form } = mount({ provider: provider("openai") });
    typeInto(keyInput(container), KEY);
    submit(form);
    await settle();
    expect(JSON.stringify(written)).not.toContain(KEY);
    vi.unstubAllGlobals();
    for (const spy of spies) expect(JSON.stringify(spy.mock.calls)).not.toContain(KEY);
    expect(window.location.href).not.toContain(KEY);
  });

  it("is a form that posts, so a key could never end up in a query string", () => {
    const { form } = mount({ provider: provider("openai") });
    expect(form.method).toBe("post");
  });
});
