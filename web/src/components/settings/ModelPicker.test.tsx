// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { FakeApiError, calls, callsTo, on, reset } from "../../testing/fakeApi";
import { pick, settle, text, typeInto } from "../../testing/forms";
import { click, query, render } from "../../testing/render";
import { ModelPicker, type ModelPickerProps } from "./ModelPicker";

vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);

const MODELS = [
  { id: "gpt-5.5", name: "GPT 5.5", context_length: 1_048_576, created: 1_790_000_300 },
  { id: "gpt-5.4-mini", name: "", context_length: 128_000, created: 1_790_000_200 },
  { id: "o3", name: "o3 (reasoning)", context_length: null, created: null },
];

function listing(extra: Record<string, unknown> = {}) {
  return { provider: "openai", listing: "live", truncated: false, models: MODELS, ...extra };
}

const CHOSEN_ROW = { id: "c1", service: "llm", provider: "openai", model: "gpt-5.5", is_validated: true, updated_at: "2026-10-18T10:00:00Z", scope: null, setup_state: "ready" };

function mount(overrides: Partial<ModelPickerProps> = {}) {
  const onChosen = vi.fn();
  const onClose = vi.fn();
  const rendered = render(<ModelPicker providerId="openai" providerName="OpenAI" currentModel={null} onChosen={onChosen} onClose={onClose} {...overrides} />);
  return { ...rendered, onChosen, onClose };
}

const rows = (root: ParentNode) => [...root.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
const useButton = (root: ParentNode) => [...root.querySelectorAll("button")].find((b) => /Use this model|Checking\.\.\./.test(text(b))) as HTMLButtonElement;

beforeEach(() => {
  reset();
});

describe("loading the list", () => {
  it("asks for the models of the saved key, not of a key from the page, and shows a placeholder while it waits", async () => {
    on("POST /credentials/llm/models", () => new Promise(() => undefined));
    const { container } = mount();
    await settle(1);
    expect(callsTo("POST /credentials/llm/models")[0].body).toEqual({ provider: "openai" });
    expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull();
    expect(text(container)).toContain("Loading OpenAI's models...");
  });

  it("lists the models in the order the API gave, with the id in mono and the context length where returned", async () => {
    on("POST /credentials/llm/models", listing());
    const { container } = mount();
    await settle();
    expect(rows(container).map((r) => r.value)).toEqual(["gpt-5.5", "gpt-5.4-mini", "o3"]);
    const first = query(container, ".bj-set-model");
    expect(text(first)).toContain("GPT 5.5");
    expect(text(first)).toContain("1M tokens");
    expect(query(first, ".bj-set-mono").textContent).toBe("gpt-5.5");
    // a model without a name shows its id once; one without a context length shows none
    const second = container.querySelectorAll(".bj-set-model")[1];
    expect(second.querySelector(".bj-set-model-name")).toBeNull();
    expect(query(second, ".bj-set-mono").textContent).toBe("gpt-5.4-mini");
    expect(query(second, ".bj-set-model-context").textContent).toBe("128K tokens");
    const third = container.querySelectorAll(".bj-set-model")[2];
    expect(query(third, ".bj-set-model-name").textContent).toBe("o3 (reasoning)");
    expect(query(third, ".bj-set-mono").textContent).toBe("o3");
    expect(third.querySelector(".bj-set-model-context")).toBeNull();
    expect(text(container)).toContain("3 models, newest first.");
  });

  it("says plainly when the provider returned no models, and when it has no list at all", async () => {
    on("POST /credentials/llm/models", listing({ models: [] }));
    const empty = mount();
    await settle();
    expect(text(empty.container)).toContain("OpenAI returned no models for this key.");
    empty.unmount();

    on("POST /credentials/llm/models", listing({ listing: "unavailable", models: [] }));
    const unavailable = mount({ providerId: "custom", providerName: "Groq" });
    await settle();
    expect(text(unavailable.container)).toContain("Groq doesn't give out a list of its models for this key");
    expect(unavailable.container.querySelector('input[type="radio"]')).toBeNull();
    expect(unavailable.container.querySelector('input[type="search"]')).toBeNull();
    // the escape hatch is the only way in, and it is there
    expect(unavailable.container.querySelector('input[type="text"]')).not.toBeNull();
  });

  it("says the list was cut when the API says so", async () => {
    on("POST /credentials/llm/models", listing({ truncated: true }));
    const { container } = mount();
    await settle();
    expect(text(container)).toContain("OpenAI has more models than this list holds: it is cut to the newest 3.");
  });

  it("shows a failure in the provider's name with a way to try again, and the typed id still works", async () => {
    on("POST /credentials/llm/models", new FakeApiError("x", "PROVIDER_UNAVAILABLE", "timeout", 502, true));
    const { container } = mount();
    await settle();
    const alert = query(container, '[role="alert"]');
    expect(text(alert)).toContain("OpenAI took too long to answer");
    expect(container.querySelector('input[type="radio"]')).toBeNull();
    expect(container.querySelector('input[placeholder="provider/model-name"]')).not.toBeNull();

    on("POST /credentials/llm/models", listing());
    click([...alert.querySelectorAll("button")].find((b) => /Try loading/.test(text(b))) as HTMLButtonElement);
    await settle();
    expect(rows(container)).toHaveLength(3);
    expect(callsTo("POST /credentials/llm/models")).toHaveLength(2);
  });

  it("reads an answer that is not a model list as a failure", async () => {
    on("POST /credentials/llm/models", { nope: true });
    const { container } = mount();
    await settle();
    expect(text(query(container, '[role="alert"]'))).toContain("in a form we couldn't read");
  });

  it("does not ask again when the catalogue finishes loading and the provider's name changes", async () => {
    on("POST /credentials/llm/models", listing());
    const { rerender, onChosen } = mount({ providerName: "openai" });
    await settle();
    rerender(<ModelPicker providerId="openai" providerName="OpenAI" currentModel={null} onChosen={onChosen} />);
    await settle();
    expect(callsTo("POST /credentials/llm/models")).toHaveLength(1);
  });
});

describe("filtering a long list", () => {
  const many = Array.from({ length: 250 }, (_, i) => ({ id: `vendor/model-${String(i).padStart(3, "0")}`, name: `Model ${i}`, context_length: 8192, created: 1_790_000_000 - i }));

  it("draws the first hundred and says how many there are, and the filter reaches the rest", async () => {
    on("POST /credentials/llm/models", listing({ models: many }));
    const { container } = mount();
    await settle();
    expect(rows(container)).toHaveLength(100);
    expect(text(container)).toContain("Showing the first 100 of 250 matches, newest first. Filter to narrow it.");
    typeInto(query<HTMLInputElement>(container, 'input[type="search"]'), "model-249");
    expect(rows(container).map((r) => r.value)).toEqual(["vendor/model-249"]);
    expect(text(container)).toContain("1 model, newest first.");
    typeInto(query<HTMLInputElement>(container, 'input[type="search"]'), "zzz");
    expect(text(container)).toContain("No model matches that.");
  });
});

describe("a stranger's text", () => {
  it("is drawn as text: a name that is markup creates no element, and an id that could not be chosen is not offered", async () => {
    on(
      "POST /credentials/llm/models",
      listing({
        models: [
          { id: "ok/model", name: '<img src=x onerror="window.__pwned=1"><b>bold</b>', context_length: null, created: null },
          { id: '"><img src=x onerror=alert(1)>', name: "bad id", context_length: null, created: null },
        ],
      }),
    );
    const { container } = mount();
    await settle();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".bj-set-model b")).toBeNull();
    expect(text(query(container, ".bj-set-model"))).toContain('<img src=x onerror="window.__pwned=1"><b>bold</b>');
    expect(rows(container).map((r) => r.value)).toEqual(["ok/model"]);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

describe("choosing a model", () => {
  it("keeps the button off until there is an id that could be a model, then sends that id and nothing else", async () => {
    on("POST /credentials/llm/models", listing());
    on("PUT /credentials/llm/openai/model", CHOSEN_ROW);
    const { container, onChosen } = mount();
    await settle();
    expect(useButton(container).disabled).toBe(true);
    pick(rows(container)[0]);
    expect(rows(container)[0].checked).toBe(true);
    expect((query(container, 'input[placeholder="provider/model-name"]') as HTMLInputElement).value).toBe("gpt-5.5");
    expect(useButton(container).disabled).toBe(false);
    click(useButton(container));
    await settle();
    expect(callsTo("PUT /credentials/llm/openai/model")[0].body).toEqual({ model: "gpt-5.5" });
    expect(onChosen).toHaveBeenCalledWith(expect.objectContaining({ model: "gpt-5.5", setup_state: "ready" }), "gpt-5.5");
  });

  it("shows progress while the one real request is out, and does not send a second one", async () => {
    on("POST /credentials/llm/models", listing());
    let finish: (value: unknown) => void = () => undefined;
    on("PUT /credentials/llm/openai/model", () => new Promise((resolve) => (finish = resolve)));
    const { container } = mount();
    await settle();
    pick(rows(container)[1]);
    click(useButton(container));
    await settle(1);
    expect(text(container)).toContain("Checking gpt-5.4-mini with OpenAI: one tiny real request...");
    expect(useButton(container).getAttribute("aria-disabled")).toBe("true");
    click(useButton(container));
    await settle(1);
    expect(callsTo("PUT /credentials/llm/openai/model")).toHaveLength(1);
    finish(CHOSEN_ROW);
    await settle();
  });

  it("takes a typed id, which is the way in when there is no list", async () => {
    on("POST /credentials/llm/models", listing({ models: [], listing: "unavailable" }));
    on("PUT /credentials/llm/openai/model", CHOSEN_ROW);
    const { container, onChosen } = mount();
    await settle();
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="provider/model-name"]'), "my-vendor/model:free");
    click(useButton(container));
    await settle();
    expect(callsTo("PUT /credentials/llm/openai/model")[0].body).toEqual({ model: "my-vendor/model:free" });
    expect(onChosen).toHaveBeenCalled();
  });

  it("refuses an id that cannot be one without sending it", async () => {
    on("POST /credentials/llm/models", listing());
    const { container } = mount();
    await settle();
    typeInto(query<HTMLInputElement>(container, 'input[placeholder="provider/model-name"]'), "two words");
    expect(useButton(container).disabled).toBe(true);
    expect(text(container)).toContain("That doesn't look like a model id.");
    expect(query(container, 'input[placeholder="provider/model-name"]').getAttribute("aria-invalid")).toBe("true");
  });

  it("says why the provider refused a model, keeps the choice so it can be changed, and changes nothing", async () => {
    on("POST /credentials/llm/models", listing());
    on("PUT /credentials/llm/openai/model", new FakeApiError("x", "PROVIDER_REJECTED", "model_not_found"));
    const { container, onChosen } = mount();
    await settle();
    pick(rows(container)[2]);
    click(useButton(container));
    await settle();
    expect(text(query(container, '[role="alert"]'))).toContain("OpenAI has no model with that id on this account.");
    expect(text(query(container, '[role="alert"]'))).toContain("Your default model was not changed.");
    expect(onChosen).not.toHaveBeenCalled();
    expect(rows(container)[2].checked).toBe(true);
  });

  it.each([
    ["out_of_credit", /no credit left.*Add credit with OpenAI/],
    ["model_retired", /retired that model/],
    ["key_rejected", /no longer accepts the key you saved/],
    ["rate_limited", /rate limiting this key/],
  ])("words a %s refusal in OpenAI's name", async (reason, pattern) => {
    on("POST /credentials/llm/models", listing());
    on("PUT /credentials/llm/openai/model", new FakeApiError("x", "PROVIDER_REJECTED", reason));
    const { container } = mount();
    await settle();
    pick(rows(container)[0]);
    click(useButton(container));
    await settle();
    expect(text(query(container, '[role="alert"]'))).toMatch(pattern);
  });

  it("is titled for a first choice or a change, and says a key without a model cannot be used", async () => {
    on("POST /credentials/llm/models", listing());
    const first = mount();
    await settle();
    expect(text(query(first.container, "h3"))).toBe("Choose a model for OpenAI");
    expect(text(first.container)).toContain("It can't be used until you choose a model.");
    first.unmount();
    const change = mount({ currentModel: "gpt-5.5" });
    await settle();
    expect(text(query(change.container, "h3"))).toBe("Change the model for OpenAI");
    expect((query(change.container, 'input[placeholder="provider/model-name"]') as HTMLInputElement).value).toBe("gpt-5.5");
    expect(text(change.container)).not.toContain("It can't be used until");
    click(query(change.container, "button.bj-quiet"));
    expect(change.onClose).toHaveBeenCalled();
  });

  it("has no cyan button, and is one tab stop for the whole list", async () => {
    on("POST /credentials/llm/models", listing());
    const { container } = mount();
    await settle();
    expect(container.querySelector(".bj-primary")).toBeNull();
    // native radios in one group: the arrow keys move through them
    expect(new Set(rows(container).map((r) => r.name)).size).toBe(1);
    expect(calls.every((c) => c.method !== "PUT")).toBe(true);
  });

  it("has no serious accessibility violation, loading, listed or failed", async () => {
    on("POST /credentials/llm/models", listing());
    const listed = mount();
    await expectNoSeriousA11yViolations(listed.container);
    await settle();
    await expectNoSeriousA11yViolations(listed.container);
    listed.unmount();
    on("POST /credentials/llm/models", new FakeApiError("x", "PROVIDER_REJECTED", "key_rejected"));
    const failed = mount();
    await settle();
    await expectNoSeriousA11yViolations(failed.container);
  });
});
