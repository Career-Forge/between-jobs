import { describe, expect, it } from "vitest";
import { CONNECT_MODEL_WORDS, modelKeyDetailOf, modelKeyOf, modelStatusWords } from "./modelKey";

describe("the words for an AI model", () => {
  it("say what is connected and what is missing, naming the provider", () => {
    expect(CONNECT_MODEL_WORDS).toBe("Connect an AI model");
    expect(modelStatusWords("ready", "OpenRouter")).toBe("OpenRouter, model chosen");
    expect(modelStatusWords("needs_model", "OpenAI")).toBe("Choose a model for OpenAI");
    expect(modelStatusWords("needs_key", "Anthropic")).toBe("Add a key for Anthropic");
  });

  it("use no technical word", () => {
    for (const state of ["ready", "needs_model", "needs_key"] as const) {
      expect(modelStatusWords(state, "X")).not.toMatch(/setup_state|needs_|\bLLM\b|model key set/i);
    }
  });
});

describe("modelKeyDetailOf", () => {
  const llm = (provider: string, extra: Record<string, unknown> = {}) => ({ service: "llm", provider, is_validated: true, model: null, ...extra });

  it("is empty for no model key, and ignores search keys", () => {
    expect(modelKeyDetailOf([])).toEqual({ reading: "none", providers: [] });
    expect(modelKeyDetailOf([{ service: "search", provider: "brave", is_validated: true }])).toEqual({ reading: "none", providers: [] });
  });

  it("names the providers in the state the reading names, in the order the rows came", () => {
    const rows = [llm("openai"), llm("openrouter", { model: "a/b" }), llm("anthropic", { model: "c" })];
    expect(modelKeyOf(rows)).toBe("ready");
    expect(modelKeyDetailOf(rows)).toEqual({ reading: "ready", providers: ["OpenRouter", "Anthropic"] });
    expect(modelKeyDetailOf([llm("openai"), llm("qwen", { is_validated: false })])).toEqual({ reading: "needs_model", providers: ["OpenAI"] });
    expect(modelKeyDetailOf([llm("qwen", { is_validated: false })])).toEqual({ reading: "needs_key", providers: ["Qwen Cloud"] });
  });

  it("follows the server's setup_state when it sends one", () => {
    expect(modelKeyDetailOf([llm("openai", { setup_state: "needs_model", model: "x" })])).toEqual({ reading: "needs_model", providers: ["OpenAI"] });
  });

  it("shows a provider it has no name for as the server sent it", () => {
    expect(modelKeyDetailOf([llm("newhost", { model: "m" })])).toEqual({ reading: "ready", providers: ["newhost"] });
  });
});
