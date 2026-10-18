// @vitest-environment jsdom
// @vitest-environment-options {"url": "https://app.example/settings"}
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { callsTo, on, reset } from "../../testing/fakeApi";
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
    connectStore.receive({ kind: "ready", code: "the-code-123", verifier: "the-verifier" });
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

  it("does nothing when nothing came back", async () => {
    mount({ navigate: vi.fn(), crypto: webCrypto });
    await settle();
    expect(callsTo("POST /credentials/openrouter/exchange")).toHaveLength(0);
  });
});
