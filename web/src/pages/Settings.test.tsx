// @vitest-environment jsdom
// @vitest-environment-options {"url": "https://app.example/settings"}
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectStore } from "../lib/settings/openrouterOAuth";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, calls, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { click, query, render } from "../testing/render";
import Settings from "./Settings";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);
vi.mock("../lib/useCapabilities", () => ({
  useCapabilities: () => ({ kind: "checking" }),
  refreshCapabilities: () => undefined,
}));
vi.mock("../lib/useHiringSignalsStatus", () => ({ useHiringSignalsEnabled: () => false }));
vi.mock("../components/SavedSearchesCard", () => ({ SavedSearchesCard: () => <div className="bj-card">Saved searches stand-in</div> }));

// The sign-in starts the real flow, but the window must not leave: the address is recorded.
const departures: string[] = [];
vi.mock("../lib/settings/useOpenRouterConnect", async (importOriginal) => {
  const real = await importOriginal<typeof import("../lib/settings/useOpenRouterConnect")>();
  const webCrypto = globalThis.crypto as unknown as import("../lib/settings/openrouterOAuth").CryptoLike;
  return { ...real, useOpenRouterConnect: () => real.useOpenRouterConnect({ navigate: (url) => void departures.push(url), crypto: webCrypto }) };
});

const CATALOG = {
  providers: [
    { id: "openrouter", name: "OpenRouter", key_hint: "Starts with sk-or-.", get_key_url: "https://openrouter.ai/keys", manage_url: "https://openrouter.ai/settings/keys", model_list: "live" },
    { id: "openai", name: "OpenAI", key_hint: "Usually starts with sk-.", get_key_url: "https://platform.openai.com/api-keys", manage_url: null, model_list: "live" },
    { id: "custom", name: "Other OpenAI-compatible provider", key_hint: "", get_key_url: null, manage_url: null, model_list: "best_effort", endpoints: [{ id: "groq", name: "Groq", base_url: "https://api.groq.com/openai/v1", get_key_url: "https://console.groq.com/keys" }] },
  ],
};

const MODELS = { provider: "openrouter", listing: "live", truncated: false, models: [{ id: "vendor/model-a", name: "Model A", context_length: 200000, created: 1_790_000_000 }] };

function key(provider: string, extra: Record<string, unknown> = {}) {
  return { id: `id-${provider}`, service: "llm", provider, model: null, is_validated: true, updated_at: "2026-10-18T10:00:00Z", scope: null, ...extra };
}

function describeAccount(credentials: unknown[] | Error, profile: unknown | Error = {}) {
  on("GET /credentials", credentials);
  on("GET /profile/current", profile);
  on("GET /credentials/llm/providers", CATALOG);
}

function page() {
  return render(
    <MemoryRouter initialEntries={["/settings"]}>
      <Settings />
    </MemoryRouter>,
  );
}

const band = (root: ParentNode) => query(root, 'section[aria-label="Next step"]');
const buttonIn = (root: ParentNode, label: string | RegExp) =>
  [...root.querySelectorAll("button, a")].find((b) => (typeof label === "string" ? text(b) === label : label.test(text(b)))) as HTMLElement;

beforeEach(() => {
  reset();
  departures.length = 0;
  window.sessionStorage.clear();
});

afterEach(() => {
  connectStore.acknowledge();
  vi.restoreAllMocks();
});

describe("the page", () => {
  it("has one h1, Settings, and the sections in order", async () => {
    describeAccount([key("openrouter", { model: "m", setup_state: "ready" })]);
    const { container } = page();
    await settle();
    expect([...container.querySelectorAll("h1")].map((h) => h.textContent)).toEqual(["Settings"]);
    expect([...container.querySelectorAll("section > h2, .bj-set-section-head > h2")].map((h) => h.textContent)).toEqual([
      "AI provider",
      "Search and data keys",
      "Connections",
    ]);
  });

  it("shows the band loading and placeholders while it reads, and lights nothing", async () => {
    on("GET /credentials", () => new Promise(() => undefined));
    on("GET /profile/current", () => new Promise(() => undefined));
    on("GET /credentials/llm/providers", CATALOG);
    const { container } = page();
    await settle(1);
    expect(text(band(container))).toContain("Loading...");
    expect(container.querySelector(".bj-primary")).toBeNull();
    expect(text(container)).toContain("Loading your saved keys...");
  });
});

describe("arriving at #ai-provider (the first-run step's link)", () => {
  it("leaves keyboard focus on the section heading, not at the top of the page", async () => {
    Element.prototype.scrollIntoView = () => undefined;
    describeAccount([key("openai", { setup_state: "needs_model" })]);
    const { container } = render(
      <MemoryRouter initialEntries={["/settings#ai-provider"]}>
        <Settings />
      </MemoryRouter>,
    );
    await settle();
    expect(document.activeElement).toBe(query(container, "#ai-provider-h"));
  });
});

describe("with no model key", () => {
  it("lights the band with Connect with OpenRouter, the page's only cyan, and outlines the same button in the section", async () => {
    describeAccount([]);
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Add a model key so Between Jobs can draft and score.");
    expect(text(band(container))).toContain("why: profile active · no model key saved");
    const primaries = [...container.querySelectorAll(".bj-primary, .bj-button-link-primary")];
    expect(primaries).toHaveLength(1);
    expect(band(container).contains(primaries[0])).toBe(true);
    expect(text(primaries[0])).toBe("Connect with OpenRouter");
    const inSection = [...container.querySelectorAll("#ai-provider button")].find((b) => text(b) === "Connect with OpenRouter");
    expect(inSection).toBeDefined();
    expect(inSection?.classList.contains("bj-primary")).toBe(false);
  });

  it("starts the sign-in from either button: the same flow, a challenge in the address and the verifier kept for the return", async () => {
    describeAccount([]);
    const { container } = page();
    await settle();
    click(buttonIn(band(container), "Connect with OpenRouter"));
    await settle();
    expect(departures).toHaveLength(1);
    const url = new URL(departures[0]);
    expect(url.origin + url.pathname).toBe("https://openrouter.ai/auth");
    expect(url.searchParams.get("callback_url")).toBe("https://app.example/settings");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    const stored = JSON.parse(window.sessionStorage.getItem("between-jobs:openrouter-oauth") ?? "null");
    expect(stored.state).toBe(url.searchParams.get("state"));
    expect(departures[0]).not.toContain(stored.verifier);
    expect(globalThis.localStorage?.getItem("between-jobs:openrouter-oauth") ?? null).toBeNull();
  });

  it("also lists the providers to connect instead, as chips with OpenRouter pressed first", async () => {
    describeAccount([]);
    const { container } = page();
    await settle();
    const chips = [...container.querySelectorAll(".bj-set-chips button")];
    expect(chips.map((c) => text(c))).toEqual(["OpenRouter", "OpenAI", "Other OpenAI-compatible provider"]);
    expect(chips[0].getAttribute("aria-pressed")).toBe("true");
  });
});

describe("with a key that has no model", () => {
  it("says it needs one, in the band and on its row, and the band's button opens the model list", async () => {
    describeAccount([key("openai", { setup_state: "needs_model" })]);
    on("POST /credentials/llm/models", { ...MODELS, provider: "openai" });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Your OpenAI key is saved. Choose a model to finish.");
    expect(text(query(container, "#provider-openai"))).toContain("Needs a model");
    click(buttonIn(band(container), "Choose a model"));
    await settle();
    expect(query(container, "#provider-openai .bj-set-picker")).toBeDefined();
    expect(callsTo("POST /credentials/llm/models")[0].body).toEqual({ provider: "openai" });
    expect(container.querySelectorAll(".bj-primary")).toHaveLength(1);
  });

  it("lets the person choose a model, which makes the key ready and the band says so", async () => {
    describeAccount([key("openai", { setup_state: "needs_model" })]);
    on("POST /credentials/llm/models", { ...MODELS, provider: "openai" });
    const { container } = page();
    await settle();
    click(buttonIn(band(container), "Choose a model"));
    await settle();
    on("PUT /credentials/llm/openai/model", key("openai", { model: "vendor/model-a", setup_state: "ready" }));
    on("GET /credentials", [key("openai", { model: "vendor/model-a", setup_state: "ready" })]);
    click(query<HTMLInputElement>(container, 'input[type="radio"]'));
    click(buttonIn(container, "Use this model"));
    await settle();
    expect(callsTo("PUT /credentials/llm/openai/model")[0].body).toEqual({ model: "vendor/model-a" });
    expect(text(band(container))).toContain("Your model is set. Search for a role next.");
    expect(text(band(container))).toContain("OpenAI model chosen this visit");
    expect(container.querySelector(".bj-set-picker")).toBeNull();
    expect(text(query(container, "#provider-openai"))).toContain("✓ Validated");
  });
});

describe("the polite announcement", () => {
  it("changes what the live region holds even when the same words are said twice, so the second is read too", async () => {
    describeAccount([key("openai", { model: "vendor/model-a", setup_state: "ready" })]);
    on("POST /credentials/llm/models", { ...MODELS, provider: "openai" });
    on("PUT /credentials/llm/openai/model", key("openai", { model: "vendor/model-a", setup_state: "ready" }));
    const { container } = page();
    await settle();
    const region = query(container, 'p.bj-visually-hidden[role="status"]');
    const seen: string[] = [];
    new MutationObserver(() => seen.push(region.textContent ?? "")).observe(region, { childList: true, characterData: true, subtree: true });
    for (let round = 0; round < 2; round++) {
      click(buttonIn(query(container, "#provider-openai"), "Change model"));
      await settle();
      click(query<HTMLInputElement>(container, 'input[type="radio"]'));
      click(buttonIn(container, "Use this model"));
      await settle();
    }
    await settle();
    const said = seen.filter((value) => value.includes("vendor/model-a is now your default model."));
    expect(said.length).toBeGreaterThanOrEqual(2);
    expect(new Set(said).size).toBe(2);
  });
});

describe("a key replaced at another address", () => {
  it("claims no default and asks for a model, because the API may have cleared the default", async () => {
    const qwen = { id: "qwen", name: "Qwen Cloud", key_hint: "", get_key_url: "https://home.qwencloud.com/api-keys", manage_url: null, model_list: "best_effort", regions: [{ id: "qwencloud", name: "QwenCloud", needs_workspace: false, get_key_url: "https://home.qwencloud.com/api-keys" }] };
    describeAccount([key("openai", { model: "gpt-x", setup_state: "ready" }), key("qwen", { model: "qwen3-max", setup_state: "ready", base_url: "https://maas.qwencloudapi.com/compatible-mode/v1" })]);
    on("GET /credentials/llm/providers", { providers: [...CATALOG.providers, qwen] });
    on("POST /credentials/llm/models", { provider: "qwen", listing: "unavailable", truncated: false, models: [] });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Your model is set.");
    // the replacement lands at another address, so the API returns it with no model
    on("POST /credentials", key("qwen", { setup_state: "needs_model", base_url: "https://maas.qwencloudapi.com/compatible-mode/v1" }));
    on("GET /credentials", [key("openai", { model: "gpt-x", setup_state: "ready" }), key("qwen", { setup_state: "needs_model" })]);
    click(buttonIn(query(container, "#provider-qwen"), "Replace key"));
    const form = query(container, "#provider-qwen form");
    const input = query<HTMLInputElement>(form, 'input[placeholder="Paste your key"]');
    const { typeInto } = await import("../testing/forms");
    typeInto(input, "sk-" + "q".repeat(32));
    click(buttonIn(form, "Validate and save"));
    await settle();
    expect(text(band(container))).toContain("Your Qwen Cloud key is saved. Choose a model to finish.");
    expect(text(band(container))).not.toContain("Your model is set");
    expect(text(query(container, "#default-model"))).not.toContain("gpt-x on OpenAI.");
    expect(text(query(container, "#default-model"))).toContain("can't tell whether it is your default");
  });
});

describe("a model chosen on this visit", () => {
  it("is no longer called chosen once its key has been removed", async () => {
    describeAccount([key("openai", { setup_state: "needs_model" }), key("openrouter", { model: "m", setup_state: "ready" })]);
    on("POST /credentials/llm/models", { ...MODELS, provider: "openai" });
    const { container } = page();
    await settle();
    click(buttonIn(query(container, "#provider-openai"), "Choose a model"));
    await settle();
    on("PUT /credentials/llm/openai/model", key("openai", { model: "vendor/model-a", setup_state: "ready" }));
    on("GET /credentials", [key("openai", { model: "vendor/model-a", setup_state: "ready" }), key("openrouter", { model: "m", setup_state: "ready" })]);
    click(query<HTMLInputElement>(container, 'input[type="radio"]'));
    click(buttonIn(container, "Use this model"));
    await settle();
    expect(text(band(container))).toContain("OpenAI model chosen this visit");
    on("DELETE /credentials/llm/openai", undefined);
    on("GET /credentials", [key("openrouter", { model: "m", setup_state: "ready" })]);
    click(buttonIn(query(container, "#provider-openai"), "Remove key"));
    click(buttonIn(query(container, "#provider-openai"), "Remove key"));
    await settle();
    expect(text(band(container))).not.toContain("model chosen this visit");
    expect(text(band(container))).toContain("OpenRouter ready");
  });
});

describe("with a model ready", () => {
  it("is unlit: nothing needs you, and nothing on the page is cyan", async () => {
    describeAccount([key("openrouter", { model: "vendor/model-a", setup_state: "ready" })]);
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Your model is set. Everything else here is optional.");
    expect(text(band(container))).toContain("why: OpenRouter ready · 0 of 7 search keys connected");
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
    expect(text(query(container, "#default-model"))).toContain("vendor/model-a on OpenRouter.");
  });

  it("counts the search keys that are connected", async () => {
    describeAccount([
      key("openrouter", { model: "m", setup_state: "ready" }),
      { ...key("brave"), service: "search" },
      { ...key("exa"), service: "search" },
    ]);
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("2 of 7 search keys connected");
    expect(text(query(container, ".bj-set-count"))).toBe("2 of 7 connected");
  });
});

describe("when the account cannot be read", () => {
  it("says the setup check failed, keeps the keys out of sight rather than say 'Not set', and Try again reads again", async () => {
    describeAccount(new FakeApiError("down", undefined, undefined, 503));
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Couldn't check your setup. You didn't do anything wrong, and nothing was lost.");
    expect(text(band(container))).toMatch(/why: GET \/credentials failed · \d\d:\d\d/);
    expect(text(container)).toContain("Couldn't read your saved keys, so none are shown.");
    expect(container.querySelector("#search-keys")).toBeNull();
    expect(text(container)).not.toContain("Not set");
    expect(container.querySelector("#provider-openai")).toBeNull();

    on("GET /credentials", [key("openrouter", { model: "m", setup_state: "ready" })]);
    click(buttonIn(band(container), "Try again"));
    await settle();
    expect(text(band(container))).toContain("Your model is set.");
    expect(container.querySelector("#search-keys")).not.toBeNull();
  });

  it("sends a person with no profile to it first; the connect button is then outlined like the rest", async () => {
    describeAccount([], new FakeApiError("none", "NOT_FOUND", undefined, 404));
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Everything here starts from your profile. Import it first.");
    const primaries = [...container.querySelectorAll(".bj-button-link-primary, .bj-primary")];
    expect(primaries.map((p) => text(p))).toEqual(["Set up your profile"]);
  });

  it("keeps the rows and says the list may be out of date when a re-read after a change fails", async () => {
    describeAccount([{ ...key("brave"), service: "search" }]);
    on("DELETE /credentials/search/brave", undefined);
    const { container } = page();
    await settle();
    on("GET /credentials", new TypeError("Failed to fetch"));
    click(buttonIn(query(container, "#search-brave"), "Remove key"));
    click(buttonIn(query(container, "#search-brave"), "Remove key"));
    await settle();
    expect(text(container)).toContain("That change went through, but your keys couldn't be read again");
    expect(container.querySelector("#search-brave")).not.toBeNull();
  });
});

describe("coming back from OpenRouter", () => {
  const CODE = "code-" + "c".repeat(20);
  const VERIFIER = "v".repeat(60);

  it("exchanges the code on the server, re-reads, and opens the model list for the new key", async () => {
    describeAccount([]);
    on("POST /credentials/openrouter/exchange", key("openrouter", { setup_state: "needs_model" }));
    on("POST /credentials/llm/models", MODELS);
    connectStore.receive({ kind: "ready", code: CODE, verifier: VERIFIER, expiresAt: Date.now() + 600_000 });
    const { container } = page();
    await settle();
    // After the exchange the next GET /credentials shows the key.
    expect(callsTo("POST /credentials/openrouter/exchange")).toHaveLength(1);
    expect(callsTo("POST /credentials/openrouter/exchange")[0].body).toEqual({ code: CODE, code_verifier: VERIFIER });
    expect(callsTo("GET /credentials").length).toBeGreaterThanOrEqual(2);
    expect(window.location.search).toBe("");
    expect(container.innerHTML).not.toContain(CODE);
    expect(container.innerHTML).not.toContain(VERIFIER);
  });

  it("shows the key it just received as needing a model, with the model list open and the focus on it", async () => {
    let reads = 0;
    on("GET /credentials", () => (reads++ === 0 ? [] : [key("openrouter", { setup_state: "needs_model" })]));
    on("GET /profile/current", {});
    on("GET /credentials/llm/providers", CATALOG);
    on("POST /credentials/openrouter/exchange", key("openrouter", { setup_state: "needs_model" }));
    on("POST /credentials/llm/models", MODELS);
    connectStore.receive({ kind: "ready", code: CODE, verifier: VERIFIER, expiresAt: Date.now() + 600_000 });
    const { container } = page();
    await settle(5);
    expect(text(query(container, "#openrouter"))).toContain("Needs a model");
    expect(query(container, "#openrouter .bj-set-picker")).toBeDefined();
    expect(text(band(container))).toContain("Your OpenRouter key is saved. Choose a model to finish.");
    expect(container.querySelector('p[role="status"]')?.textContent).toContain("OpenRouter is connected. Choose a model to finish.");
    expect(connectStore.getSnapshot().phase).toBe("idle");
  });

  it("says the model is unchanged when a reconnection brings back a key that already has one", async () => {
    let reads = 0;
    const ready = key("openrouter", { model: "vendor/model-a", setup_state: "ready" });
    on("GET /credentials", () => (reads++ === 0 ? [ready] : [ready]));
    on("GET /profile/current", {});
    on("GET /credentials/llm/providers", CATALOG);
    on("POST /credentials/openrouter/exchange", ready);
    connectStore.receive({ kind: "ready", code: CODE, verifier: VERIFIER, expiresAt: Date.now() + 600_000 });
    const { container } = page();
    await settle(5);
    expect(container.querySelector('p[role="status"]')?.textContent).toContain("OpenRouter is connected again. Your model is unchanged.");
    expect(container.querySelector(".bj-set-picker")).toBeNull();
  });

  it("says nothing was connected, and why, for a state that did not match, and offers to connect again", async () => {
    describeAccount([]);
    connectStore.receive({ kind: "failed", reason: "state_mismatch" });
    const { container } = page();
    await settle();
    const alert = [...container.querySelectorAll('[role="alert"]')].find((a) => text(a).includes("didn't match the one this tab started"));
    expect(alert).toBeDefined();
    expect(text(alert as Element)).toContain("Nothing was connected.");
    expect(callsTo("POST /credentials/openrouter/exchange")).toHaveLength(0);
    click(buttonIn(alert as Element, "Connect with OpenRouter again"));
    await settle();
    expect(departures).toHaveLength(1);
  });

  it.each([
    ["denied", /didn't allow the connection at OpenRouter/],
    ["expired", /ran out of time/],
    ["no_pending", /didn't start that sign-in/],
    ["no_code", /without finishing the sign-in/],
  ] as const)("words a %s return, and Dismiss clears it", async (reason, pattern) => {
    describeAccount([]);
    connectStore.receive({ kind: "failed", reason });
    const { container } = page();
    await settle();
    const alert = [...container.querySelectorAll('[role="alert"]')].find((a) => pattern.test(text(a))) as Element;
    expect(alert).toBeDefined();
    click(buttonIn(alert, "Dismiss"));
    await settle();
    expect([...container.querySelectorAll('[role="alert"]')].some((a) => pattern.test(text(a)))).toBe(false);
  });

  it("says OpenRouter refused the code, and that nothing was saved", async () => {
    describeAccount([]);
    on("POST /credentials/openrouter/exchange", new FakeApiError("OpenRouter did not accept that sign-in.", "PROVIDER_REJECTED", "oauth_code_rejected"));
    connectStore.receive({ kind: "ready", code: CODE, verifier: VERIFIER, expiresAt: Date.now() + 600_000 });
    const { container } = page();
    await settle();
    const alert = [...container.querySelectorAll('[role="alert"]')].find((a) => text(a).includes("didn't accept that sign-in")) as Element;
    expect(alert).toBeDefined();
    expect(buttonIn(alert, "Connect with OpenRouter again")).toBeDefined();
    expect(container.innerHTML).not.toContain(CODE);
  });

  it("offers to finish again, from memory, when the exchange only failed to complete", async () => {
    describeAccount([]);
    let attempts = 0;
    on("POST /credentials/openrouter/exchange", () => {
      attempts += 1;
      if (attempts === 1) throw new TypeError("Failed to fetch");
      return key("openrouter", { setup_state: "needs_model" });
    });
    connectStore.receive({ kind: "ready", code: CODE, verifier: VERIFIER, expiresAt: Date.now() + 600_000 });
    const { container } = page();
    await settle();
    const alert = [...container.querySelectorAll('[role="alert"]')].find((a) => text(a).includes("Try finishing it again")) as Element;
    expect(alert).toBeDefined();
    click(buttonIn(alert, "Finish connecting again"));
    await settle();
    expect(callsTo("POST /credentials/openrouter/exchange")).toHaveLength(2);
    expect(callsTo("POST /credentials/openrouter/exchange")[1].body).toEqual({ code: CODE, code_verifier: VERIFIER });
  });

  it("writes the code and the verifier to neither storage nor the console", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) => vi.spyOn(console, level).mockImplementation(() => undefined));
    describeAccount([]);
    on("POST /credentials/openrouter/exchange", key("openrouter", { setup_state: "needs_model" }));
    on("POST /credentials/llm/models", MODELS);
    connectStore.receive({ kind: "ready", code: CODE, verifier: VERIFIER, expiresAt: Date.now() + 600_000 });
    page();
    await settle(4);
    for (const store of [globalThis.localStorage, window.sessionStorage]) {
      expect(JSON.stringify({ ...store })).not.toContain(CODE);
      expect(JSON.stringify({ ...store })).not.toContain(VERIFIER);
    }
    for (const spy of spies) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain(CODE);
      expect(JSON.stringify(spy.mock.calls)).not.toContain(VERIFIER);
    }
    expect(window.location.href).not.toContain(CODE);
  });
});

describe("a key typed into the page", () => {
  it("is gone from the page, storage and console the moment it is saved", async () => {
    const SECRET = "sk-" + "p".repeat(32);
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((level) => vi.spyOn(console, level).mockImplementation(() => undefined));
    describeAccount([]);
    on("POST /credentials", key("openai", { setup_state: "needs_model" }));
    on("POST /credentials/llm/models", { ...MODELS, provider: "openai" });
    const { container } = page();
    await settle();
    click(buttonIn(container.querySelector(".bj-set-chips") as Element, "OpenAI"));
    const { typeInto, submit } = await import("../testing/forms");
    on("GET /credentials", [key("openai", { setup_state: "needs_model" })]);
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="Paste your key"]'), SECRET);
    submit(query<HTMLFormElement>(container, "form"));
    await settle(4);
    expect(callsTo("POST /credentials")[0].body).toEqual({ service: "llm", provider: "openai", secret: SECRET });
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(JSON.stringify({ ...globalThis.localStorage, ...window.sessionStorage })).not.toContain(SECRET);
    for (const spy of spies) expect(JSON.stringify(spy.mock.calls)).not.toContain(SECRET);
    // and the model list is open for the key that was just stored
    expect(text(query(container, "#provider-openai"))).toContain("Needs a model");
    expect(query(container, "#provider-openai .bj-set-picker")).toBeDefined();
    expect(calls.filter((c) => c.method === "GET" && c.path === "/credentials").length).toBeGreaterThanOrEqual(2);
  });
});

describe("accessibility", () => {
  it.each([
    ["no key", () => describeAccount([])],
    ["a key that needs a model", () => describeAccount([key("openai", { setup_state: "needs_model" })])],
    ["a model ready", () => describeAccount([key("openrouter", { model: "m", setup_state: "ready" }), { ...key("brave"), service: "search" }])],
    ["an unreadable account", () => describeAccount(new FakeApiError("down", undefined, undefined, 503))],
  ])("has no serious violation with %s", async (_name, setup) => {
    setup();
    const { container } = page();
    await settle();
    await expectNoSeriousA11yViolations(container);
  });
});
