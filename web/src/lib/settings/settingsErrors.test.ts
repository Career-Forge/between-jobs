import { describe, expect, it } from "vitest";
import llmClient from "../../../../src/between_jobs/api/llm_client.py?raw";
import providerChecks from "../../../../src/between_jobs/api/provider_checks.py?raw";
import oauthPy from "../../../../src/between_jobs/api/openrouter_oauth.py?raw";
import credentialsRoutes from "../../../../src/between_jobs/api/credentials_routes.py?raw";
import {
  NETWORK_MESSAGE,
  connectFailureView,
  isExchangeRetryable,
  isRetryable,
  settingsFailureMessage,
  type FailureContext,
} from "./settingsErrors";

// What the API's envelope looks like once api.ts has read it: an Error with these fields.
class FakeApiError extends Error {
  constructor(
    message: string,
    public readonly code?: string,
    public readonly reason?: string,
    public readonly retryable?: boolean,
    public readonly status = 400,
    public readonly retryAfterSeconds?: number,
  ) {
    super(message);
  }
}

const save: FailureContext = { action: "save_key", provider: "OpenAI" };
const choose: FailureContext = { action: "choose_model", provider: "OpenAI" };
const list: FailureContext = { action: "list_models", provider: "OpenAI" };

function say(reason: string, context: FailureContext = save): string {
  return settingsFailureMessage(new FakeApiError("the server's own words", "PROVIDER_REJECTED", reason, false), context, "fallback");
}

describe("each reason the API names", () => {
  // The reasons are read from the API's own code, so a new one cannot arrive unworded without this noticing.
  function reasonsInServerCode(): string[] {
    const fromLiteral = /ProviderReason = Literal\[([\s\S]*?)\]/.exec(llmClient)?.[1] ?? "";
    const literal = [...fromLiteral.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    const direct = [...`${credentialsRoutes}${oauthPy}${providerChecks}`.matchAll(/"reason":\s*"([a-z_]+)"/g)].map((m) => m[1]);
    return [...new Set([...literal, ...direct])];
  }

  it("has a sentence of its own, or deliberately uses the server's", () => {
    const known = reasonsInServerCode();
    expect(known.length).toBeGreaterThan(12);
    // `unknown` is the server's catch-all for a status it cannot place; it has no sentence of its own.
    const wordedHere = [
      "key_rejected", "out_of_credit", "request_rejected", "rate_limited", "in_flight_budget", "timeout", "unreachable", "provider_error",
      "empty_response", "response_too_large", "model_not_found", "model_retired", "oauth_code_rejected", "oauth_malformed", "model_required_to_check_key",
    ];
    const usesServerSentence = ["key_malformed", "base_url_refused", "unknown"];
    const unaccounted = known.filter((reason) => !wordedHere.includes(reason) && !usesServerSentence.includes(reason));
    expect(unaccounted).toEqual([]);
  });

  it.each([
    ["key_rejected", save, /OpenAI didn't accept that key\. Check that you copied all of it.*Nothing was saved\./],
    ["key_rejected", choose, /no longer accepts the key you saved\. Replace it/],
    ["out_of_credit", save, /no credit left\. Add credit with OpenAI, then try again\. Nothing was saved\./],
    ["out_of_credit", choose, /no credit left.*Your default model was not changed\./],
    ["request_rejected", save, /usually points at the account.*Check the account with OpenAI/],
    ["rate_limited", save, /rate limiting this key\. Wait a minute/],
    ["in_flight_budget", save, /rate limiting this key/],
    ["timeout", save, /took too long to answer\. Try again in a moment\./],
    ["unreachable", save, /Couldn't reach OpenAI\./],
    ["provider_error", save, /couldn't answer that right now/],
    ["empty_response", save, /in a form we couldn't read/],
    ["model_not_found", choose, /no model with that id on this account\. Pick one from the list.*not changed/],
    ["model_retired", choose, /retired that model\. Pick a current one/],
    ["model_required_to_check_key", save, /no free way to check a key.*Enter a model id/],
  ])("%s while %j says what to do", (reason, context, pattern) => {
    expect(say(reason, context as FailureContext)).toMatch(pattern as RegExp);
  });

  it("never leaves a person with a bare 'something went wrong': each sentence names the provider or an action", () => {
    for (const reason of ["key_rejected", "out_of_credit", "request_rejected", "rate_limited", "timeout", "unreachable", "provider_error", "empty_response", "model_not_found", "model_retired"]) {
      const sentence = say(reason, choose);
      expect(sentence, reason).toContain("OpenAI");
      expect(sentence, reason).not.toMatch(/something went wrong/i);
    }
  });

  it("does not say a list failure changed anything", () => {
    expect(say("rate_limited", list)).not.toMatch(/Nothing was saved|not changed/);
  });

  it("keeps the server's sentence for a malformed key and for a refused address, which name the exact problem", () => {
    const keyMalformed = new FakeApiError("That doesn't look like an API key: paste only the key itself.", "INVALID_INPUT", "key_malformed");
    expect(settingsFailureMessage(keyMalformed, save, "fallback")).toBe("That doesn't look like an API key: paste only the key itself.");
    const address = new FakeApiError("That address is not one this server sends keys to.", "INVALID_INPUT", "base_url_refused");
    expect(settingsFailureMessage(address, save, "fallback")).toContain("not one this server sends keys to");
  });

  it("words the OpenRouter sign-in refusals", () => {
    const exchange: FailureContext = { action: "exchange", provider: "OpenRouter" };
    expect(say("oauth_code_rejected", exchange)).toMatch(/OpenRouter didn't accept that sign-in.*Connect again, or paste a key instead\./);
    expect(say("oauth_malformed", exchange)).toMatch(/Connect again, or paste a key instead/);
  });
});

describe("a failure that is not a provider's refusal", () => {
  it("says it could not reach the service when the request never got there", () => {
    expect(settingsFailureMessage(new TypeError("Failed to fetch"), save, "fallback")).toBe(NETWORK_MESSAGE);
    expect(NETWORK_MESSAGE).not.toContain("fetch");
  });

  it("uses the platform's own wording for a rate limit and the server's sentence for the rest", () => {
    const limited = new FakeApiError("x", "RATE_LIMITED", undefined, undefined, 429, 120);
    expect(settingsFailureMessage(limited, save, "fallback")).toBe("You are doing that too often. Try again in about 2 minutes.");
    expect(settingsFailureMessage(new FakeApiError("Your key is saved but no model is chosen.", "SETUP_REQUIRED"), choose, "fallback")).toBe(
      "Your key is saved but no model is chosen.",
    );
  });

  it("uses the fallback for a thrown value that is not an Error", () => {
    expect(settingsFailureMessage("boom", save, "Couldn't save that key.")).toBe("Couldn't save that key.");
  });

  it("never prints the key or any provider-supplied text: only the server's own envelope message is passed through", () => {
    const error = new FakeApiError("OpenAI rejected that key.", "PROVIDER_REJECTED", "key_rejected");
    expect(settingsFailureMessage(error, save, "fallback")).not.toContain("the server's own words");
  });
});

describe("a refused key keeps the way out the server names", () => {
  const hint =
    " A key that works on more than one workspace is refused too, because this server cannot say which workspace to use -- create the key inside a single workspace.";

  it("adds the server's hint (Anthropic's workspace, Qwen's region) to the page's advice", () => {
    const anthropic = new FakeApiError(`Anthropic rejected that key.${hint}`, "PROVIDER_REJECTED", "key_rejected");
    const message = settingsFailureMessage(anthropic, { action: "save_key", provider: "Anthropic" }, "fallback");
    expect(message).toContain("Anthropic didn't accept that key. Check that you copied all of it");
    expect(message).toContain("create the key inside a single workspace.");
    expect(message.endsWith(" Nothing was saved.")).toBe(true);
  });

  it("adds nothing when the server's sentence is the bare one", () => {
    const bare = new FakeApiError("OpenAI rejected that key.", "PROVIDER_REJECTED", "key_rejected");
    expect(settingsFailureMessage(bare, save, "fallback")).toBe(
      "OpenAI didn't accept that key. Check that you copied all of it, with no spaces or quotes, and that it came from OpenAI. Nothing was saved.",
    );
  });

  it("does not write 'a Anthropic key' or 'a OpenAI key'", () => {
    for (const provider of ["Anthropic", "OpenAI", "xAI"]) {
      const message = settingsFailureMessage(new FakeApiError("x", "PROVIDER_REJECTED", "key_rejected"), { action: "save_key", provider }, "fallback");
      expect(message).not.toMatch(/\ba (Anthropic|OpenAI|xAI) key/);
    }
  });

  it("words a pasted OpenRouter key the API refused with the reason it now names, the same as any other", () => {
    const refused = new FakeApiError("OpenRouter rejected that key.", "PROVIDER_REJECTED", "key_rejected");
    expect(settingsFailureMessage(refused, { action: "save_key", provider: "OpenRouter" }, "fallback")).toMatch(
      /OpenRouter didn't accept that key\. Check that you copied all of it.*Nothing was saved\./,
    );
  });

  it("does not tell a person to re-copy a key the sign-in just issued", () => {
    const refused = new FakeApiError("OpenRouter rejected that key.", "PROVIDER_REJECTED", "key_rejected");
    expect(settingsFailureMessage(refused, { action: "exchange", provider: "OpenRouter" }, "fallback")).toBe(
      "OpenRouter didn't accept the key it issued. No key was saved. Connect again, or paste a key instead.",
    );
  });
});

describe("a model the provider turned down", () => {
  it("points at the model first when one was being chosen (OpenRouter answers a mistyped id this way)", () => {
    const message = say("request_rejected", choose);
    expect(message).toMatch(/often because of the model chosen\. Pick one from the list, or check how you spelled it/);
    expect(message).not.toMatch(/billing|region/);
    expect(message.endsWith("Your default model was not changed.")).toBe(true);
  });

  it("still points at the account for a key check, where no model is involved", () => {
    expect(say("request_rejected", save)).toMatch(/billing that isn't switched on/);
  });
});

describe("a gateway failure with no envelope", () => {
  it("says it was Between Jobs, with the status, instead of 'Request failed (503)'", () => {
    const gateway = new FakeApiError("Request failed (503)", undefined, undefined, undefined, 503);
    expect(settingsFailureMessage(gateway, save, "fallback")).toBe("Between Jobs had a problem on its side (503). Nothing was saved. Try again in a moment.");
    expect(settingsFailureMessage(gateway, choose, "fallback")).toBe("Between Jobs had a problem on its side (503). Your default model was not changed. Try again in a moment.");
    expect(settingsFailureMessage(gateway, list, "fallback")).toBe("Between Jobs had a problem on its side (503). Try again in a moment.");
  });

  it("keeps the server's own sentence when the failure has a code", () => {
    const coded = new FakeApiError("Something specific the server said.", "INTERNAL_ERROR", undefined, undefined, 500);
    expect(settingsFailureMessage(coded, save, "fallback")).toBe("Something specific the server said.");
  });
});

describe("whether a failed OpenRouter exchange can repeat the same code", () => {
  it.each([
    [new TypeError("Failed to fetch"), true],
    // the exchange itself was throttled or slow: the code was not spent
    [new FakeApiError("x", "PROVIDER_RATE_LIMITED", "rate_limited", true, 429), true],
    [new FakeApiError("x", "PROVIDER_UNAVAILABLE", "timeout", true, 502), true],
    // the key OpenRouter issued could not be checked (no reason): the code is already spent
    [new FakeApiError("OpenRouter couldn't validate that key right now. Try again in a moment.", "PROVIDER_UNAVAILABLE", undefined, true, 502), false],
    [new FakeApiError("x", "PROVIDER_REJECTED", "oauth_code_rejected", false, 400), false],
  ])("%j -> %s", (error, expected) => {
    expect(isExchangeRetryable(error)).toBe(expected);
  });
});

describe("whether asking again could change the outcome", () => {
  it.each([
    [new TypeError("Failed to fetch"), true],
    [new FakeApiError("x", "PROVIDER_UNAVAILABLE", "timeout", true, 502), true],
    [new FakeApiError("x", "PROVIDER_RATE_LIMITED", "rate_limited", true, 429), true],
    [new FakeApiError("x", "RATE_LIMITED", undefined, undefined, 429), true],
    [new FakeApiError("x", undefined, undefined, undefined, 503), true],
    [new FakeApiError("x", "PROVIDER_REJECTED", "key_rejected", false, 400), false],
    [new FakeApiError("x", "PROVIDER_REJECTED", "oauth_code_rejected", false, 400), false],
    [new FakeApiError("x", "INVALID_INPUT", "key_malformed", false, 400), false],
    [new FakeApiError("x", "PROVIDER_UNAVAILABLE", "response_too_large", false, 502), false],
    ["not an error", false],
  ])("%j -> %s", (error, expected) => {
    expect(isRetryable(error)).toBe(expected);
  });
});

describe("a sign-in that did not finish", () => {
  it.each([
    [{ kind: "denied" as const }, /didn't allow the connection.*Nothing was connected/],
    [{ kind: "state_mismatch" as const }, /didn't match the one this tab started.*Nothing was connected/],
    [{ kind: "expired" as const }, /ran out of time.*Connect again/],
    [{ kind: "no_pending" as const }, /didn't start that sign-in.*Nothing was connected/],
    [{ kind: "no_code" as const }, /without finishing the sign-in/],
  ])("%j says what happened and that nothing was connected, and starts over", (failure, pattern) => {
    const view = connectFailureView(failure);
    expect(view.message).toMatch(pattern);
    expect(view.next).toBe("again");
  });

  it("words a refused code with the API's reason, and starts over", () => {
    const view = connectFailureView({ kind: "rejected", error: new FakeApiError("x", "PROVIDER_REJECTED", "oauth_code_rejected") });
    expect(view.message).toMatch(/OpenRouter didn't accept that sign-in/);
    expect(view.next).toBe("again");
  });

  it("offers to finish again, without starting over, when the exchange itself failed to complete", () => {
    const view = connectFailureView({ kind: "retryable", error: new TypeError("Failed to fetch") });
    // one request to try again, not two
    expect(view.message).toBe("Couldn't reach Between Jobs. Check your connection. Try finishing it again; if it keeps failing, connect again.");
    expect(view.next).toBe("retry");
  });

  it("joins a sentence that has no full stop (a bare 'Request failed (503)') to the next one properly", () => {
    const view = connectFailureView({ kind: "retryable", error: new FakeApiError("Request failed (503)", undefined, undefined, undefined, 503) });
    expect(view.message).toBe(
      "Between Jobs had a problem on its side (503). No key was saved. Try again in a moment. Try finishing it again; if it keeps failing, connect again.",
    );
  });

  it("does not say a sign-in is limiting 'this key', when there is no key yet", () => {
    const view = connectFailureView({ kind: "retryable", error: new FakeApiError("x", "PROVIDER_RATE_LIMITED", "rate_limited", true, 429) });
    expect(view.message).toContain("OpenRouter is rate limiting this sign-in.");
    expect(view.message).not.toContain("this key");
  });
});
