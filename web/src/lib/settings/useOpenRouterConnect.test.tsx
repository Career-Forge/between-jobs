// @vitest-environment jsdom
// @vitest-environment-options {"url": "https://app.example/settings"}
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeApiError, callsTo, on, reset } from "../../testing/fakeApi";
import { settle } from "../../testing/forms";
import { render } from "../../testing/render";
import { connectStore, type CryptoLike } from "./openrouterOAuth";
import { UNSUPPORTED_MESSAGE, useOpenRouterConnect, type OpenRouterConnect, type OpenRouterDeps } from "./useOpenRouterConnect";

vi.mock("../api", async () => (await import("../../testing/fakeApi")).apiModule);

const webCrypto = globalThis.crypto as unknown as CryptoLike;

function Probe({ deps, expose }: { deps: OpenRouterDeps; expose: (value: OpenRouterConnect) => void }) {
  expose(useOpenRouterConnect(deps));
  return null;
}

function mount(deps: OpenRouterDeps) {
  let latest: OpenRouterConnect | null = null;
  render(<Probe deps={deps} expose={(value) => (latest = value)} />);
  return () => latest as unknown as OpenRouterConnect;
}

beforeEach(() => {
  reset();
  window.sessionStorage.clear();
});

afterEach(() => {
  connectStore.acknowledge();
});

describe("starting", () => {
  it("goes to OpenRouter with this site's Settings address as the callback, and stays busy while the window leaves", async () => {
    const navigate = vi.fn();
    const hook = mount({ navigate, crypto: webCrypto, now: () => 1_800_000_000_000, origin: () => "https://app.example" });
    hook().start();
    await settle();
    expect(navigate).toHaveBeenCalledTimes(1);
    const url = new URL(navigate.mock.calls[0][0] as string);
    expect(url.searchParams.get("callback_url")).toBe("https://app.example/settings");
    expect(hook().busy).toBe(true);
    // a second press while it is on its way does nothing
    hook().start();
    await settle();
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("frees the button, and drops the unused verifier, when the page is restored from the back/forward cache", async () => {
    const navigate = vi.fn();
    const hook = mount({ navigate, crypto: webCrypto });
    hook().start();
    await settle();
    expect(hook().busy).toBe(true);
    expect(window.sessionStorage.getItem("between-jobs:openrouter-oauth")).not.toBeNull();
    // an ordinary pageshow (the first load) changes nothing
    act(() => {
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false }));
    });
    expect(hook().busy).toBe(true);
    act(() => {
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    });
    expect(hook().busy).toBe(false);
    expect(window.sessionStorage.getItem("between-jobs:openrouter-oauth")).toBeNull();
    // and the person can start again
    hook().start();
    await settle();
    expect(navigate).toHaveBeenCalledTimes(2);
  });

  it("does nothing but say so where the browser cannot do the secure part", async () => {
    const navigate = vi.fn();
    const hook = mount({ navigate, crypto: { getRandomValues: webCrypto.getRandomValues.bind(webCrypto) } });
    hook().start();
    await settle();
    expect(navigate).not.toHaveBeenCalled();
    expect(hook().unsupported).toBe(true);
    expect(hook().error).toBe(UNSUPPORTED_MESSAGE);
    expect(hook().busy).toBe(false);
    expect(window.sessionStorage.getItem("between-jobs:openrouter-oauth")).toBeNull();
  });

  it("clears an old failure when a new sign-in starts", async () => {
    connectStore.receive({ kind: "failed", reason: "denied" });
    const hook = mount({ navigate: vi.fn(), crypto: webCrypto });
    expect(hook().result.phase).toBe("failed");
    hook().start();
    await settle();
    expect(hook().result.phase).toBe("idle");
  });
});

describe("the exchange", () => {
  it("runs once for a return that is waiting, with the code and verifier in the body only", async () => {
    on("POST /credentials/openrouter/exchange", { id: "c", service: "llm", provider: "openrouter", model: null, is_validated: true, updated_at: "", scope: null });
    connectStore.receive({ kind: "ready", code: "the-code-123", verifier: "the-verifier", expiresAt: Date.now() + 600_000 });
    const hook = mount({ navigate: vi.fn(), crypto: webCrypto });
    mount({ navigate: vi.fn(), crypto: webCrypto }); // a second mount (React's own double run) asks again
    await settle();
    expect(callsTo("POST /credentials/openrouter/exchange")).toHaveLength(1);
    expect(callsTo("POST /credentials/openrouter/exchange")[0]).toEqual({
      method: "POST",
      path: "/credentials/openrouter/exchange",
      body: { code: "the-code-123", code_verifier: "the-verifier" },
    });
    expect(hook().result.phase).toBe("connected");
  });

  it("offers to finish again when the exchange itself was throttled, because the code was not spent", async () => {
    on("POST /credentials/openrouter/exchange", new FakeApiError("x", "PROVIDER_RATE_LIMITED", "rate_limited", 429, true));
    connectStore.receive({ kind: "ready", code: "the-code-123", verifier: "the-verifier", expiresAt: Date.now() + 600_000 });
    const hook = mount({ navigate: vi.fn(), crypto: webCrypto });
    await settle();
    expect(hook().result).toMatchObject({ phase: "failed", failure: { kind: "retryable" } });
  });

  it("does not offer to repeat a code that is already spent: the check of the key OpenRouter issued failed, so start over", async () => {
    // llm_credentials_routes.exchange_openrouter_code: after the code is traded, the key check
    // can fail as "unavailable" with no reason of its own
    on("POST /credentials/openrouter/exchange", new FakeApiError("OpenRouter couldn't validate that key right now. Try again in a moment.", "PROVIDER_UNAVAILABLE", undefined, 502, true));
    connectStore.receive({ kind: "ready", code: "the-code-123", verifier: "the-verifier", expiresAt: Date.now() + 600_000 });
    const hook = mount({ navigate: vi.fn(), crypto: webCrypto });
    await settle();
    expect(hook().result).toMatchObject({ phase: "failed", failure: { kind: "rejected" } });
    hook().retry();
    await settle();
    expect(callsTo("POST /credentials/openrouter/exchange")).toHaveLength(1);
  });

  it("does nothing when nothing came back", async () => {
    mount({ navigate: vi.fn(), crypto: webCrypto });
    await settle();
    expect(callsTo("POST /credentials/openrouter/exchange")).toHaveLength(0);
  });
});
