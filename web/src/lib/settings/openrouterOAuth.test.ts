import { describe, expect, it, vi } from "vitest";
import mainSource from "../../main.tsx?raw";
import oauthSource from "./openrouterOAuth.ts?raw";
import captureSource from "./captureOAuthReturn.ts?raw";
import {
  OAUTH_KEY_LABEL,
  OAUTH_STORAGE_KEY,
  OAUTH_TTL_MS,
  OPENROUTER_AUTH_URL,
  base64Url,
  captureReturn,
  challengeFor,
  createConnectStore,
  extractReturn,
  makeState,
  makeVerifier,
  resolveReturn,
  savePending,
  startConnect,
  takePending,
  tabStorage,
  type BrowserLike,
  type ConnectState,
  type CryptoLike,
  type ReturnOutcome,
  type SessionStorageLike,
} from "./openrouterOAuth";

// Node's own Web Crypto: the same getRandomValues and SHA-256 the browser gives the page.
const webCrypto = globalThis.crypto as unknown as CryptoLike;

class MemoryStorage implements SessionStorageLike {
  readonly items = new Map<string, string>();
  readonly log: string[] = [];
  getItem(key: string) {
    this.log.push(`get ${key}`);
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.log.push(`set ${key}`);
    this.items.set(key, value);
  }
  removeItem(key: string) {
    this.log.push(`remove ${key}`);
    this.items.delete(key);
  }
}

// Code, not comments: a `//` line is prose.
function codeOnly(source: string): string {
  return source
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

const UNRESERVED = /^[A-Za-z0-9._~-]+$/;
const NOW = 1_800_000_000_000;

describe("the PKCE values (RFC 7636)", () => {
  // Appendix B of the RFC: the one published pair of a verifier and its S256 challenge.
  it("turns the RFC's test verifier into the RFC's test challenge", async () => {
    expect(await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", webCrypto)).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });

  it("encodes the RFC's test octets as the RFC's test verifier (base64url, no padding)", () => {
    const octets = [116, 24, 223, 180, 151, 153, 224, 37, 79, 250, 96, 125, 216, 173, 187, 186, 22, 212, 37, 77, 105, 214, 191, 240, 91, 88, 5, 88, 83, 132, 141, 121];
    expect(base64Url(new Uint8Array(octets))).toBe("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk");
  });

  it("makes verifiers of the length and alphabet RFC 7636 and the API allow, never twice the same", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 50; i++) {
      const verifier = makeVerifier(webCrypto);
      expect(verifier.length).toBeGreaterThanOrEqual(43);
      expect(verifier.length).toBeLessThanOrEqual(128);
      expect(verifier).toMatch(UNRESERVED);
      seen.add(verifier);
    }
    expect(seen.size).toBe(50);
  });

  it("makes a state of at least 128 bits, in a URL-safe alphabet", () => {
    const state = makeState(webCrypto);
    expect(state).toMatch(UNRESERVED);
    // 32 bytes of base64url
    expect(state.length).toBeGreaterThanOrEqual(43);
    expect(makeState(webCrypto)).not.toBe(state);
  });

  it("reads its randomness from crypto.getRandomValues and nowhere else", () => {
    expect(codeOnly(oauthSource)).toContain("crypto.getRandomValues");
    expect(codeOnly(oauthSource)).not.toContain("Math.random");
  });
});

describe("starting the sign-in", () => {
  async function start(storage: MemoryStorage | null = new MemoryStorage()) {
    const result = await startConnect({ crypto: webCrypto, storage, callbackUrl: "https://app.example/settings", now: NOW });
    return { result, storage };
  }

  it("sends the person to OpenRouter with the S256 challenge, the state, the callback and a key label", async () => {
    const { result, storage } = await start();
    if (result.kind !== "go") throw new Error("expected to go");
    const url = new URL(result.url);
    expect(`${url.origin}${url.pathname}`).toBe(OPENROUTER_AUTH_URL);
    expect(url.searchParams.get("callback_url")).toBe("https://app.example/settings");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("key_label")).toBe(OAUTH_KEY_LABEL);
    const stored = JSON.parse(storage?.items.get(OAUTH_STORAGE_KEY) ?? "null");
    expect(url.searchParams.get("state")).toBe(stored.state);
    expect(url.searchParams.get("code_challenge")).toBe(await challengeFor(stored.verifier, webCrypto));
    expect([...url.searchParams.keys()].sort()).toEqual(["callback_url", "code_challenge", "code_challenge_method", "key_label", "state"]);
  });

  it("never puts the verifier in the address it sends the person to", async () => {
    const { result, storage } = await start();
    if (result.kind !== "go") throw new Error("expected to go");
    const { verifier } = JSON.parse(storage?.items.get(OAUTH_STORAGE_KEY) ?? "null");
    expect(result.url).not.toContain(verifier);
  });

  it("keeps the verifier and state for ten minutes, in session storage", async () => {
    const { storage } = await start();
    const stored = JSON.parse(storage?.items.get(OAUTH_STORAGE_KEY) ?? "null");
    expect(stored.expiresAt).toBe(NOW + 10 * 60 * 1000);
    expect(OAUTH_TTL_MS).toBe(10 * 60 * 1000);
  });

  it("does not start when the verifier could not be kept for the return", async () => {
    expect((await start(null)).result).toEqual({ kind: "unsupported" });
    const refusing: SessionStorageLike = {
      getItem: () => null,
      setItem: () => {
        throw new Error("quota");
      },
      removeItem: () => undefined,
    };
    const result = await startConnect({ crypto: webCrypto, storage: refusing, callbackUrl: "https://app.example/settings", now: NOW });
    expect(result).toEqual({ kind: "unsupported" });
  });

  it("does not start where Web Crypto cannot hash (an insecure page)", async () => {
    const noSubtle: CryptoLike = { getRandomValues: webCrypto.getRandomValues.bind(webCrypto) };
    for (const crypto of [undefined, noSubtle]) {
      const result = await startConnect({ crypto, storage: new MemoryStorage(), callbackUrl: "https://app.example/settings", now: NOW });
      expect(result).toEqual({ kind: "unsupported" });
    }
  });

  it("uses session storage, never local storage", () => {
    expect(codeOnly(oauthSource)).toContain("sessionStorage");
    expect(codeOnly(oauthSource)).not.toContain("localStorage");
    expect(codeOnly(captureSource)).not.toContain("localStorage");
  });

  it("survives a browser whose session storage throws on access", () => {
    expect(
      tabStorage(() => {
        throw new Error("blocked");
      }),
    ).toBeNull();
  });
});

describe("what waits in storage", () => {
  const pending = { verifier: "v".repeat(43), state: "s".repeat(43), expiresAt: NOW + 1000 };

  it("is found once, and gone after it was read", () => {
    const storage = new MemoryStorage();
    savePending(storage, pending);
    expect(takePending(storage, NOW)).toEqual({ kind: "found", pending });
    expect(takePending(storage, NOW)).toEqual({ kind: "missing" });
  });

  it("is expired at the stated minute, and is removed all the same", () => {
    const storage = new MemoryStorage();
    savePending(storage, pending);
    expect(takePending(storage, pending.expiresAt)).toEqual({ kind: "expired" });
    expect(storage.items.has(OAUTH_STORAGE_KEY)).toBe(false);
  });

  it.each([["not JSON", "{oops"], ["an array", "[]"], ["a missing verifier", JSON.stringify({ state: "x", expiresAt: NOW + 5 })], ["a text expiry", JSON.stringify({ verifier: "v", state: "s", expiresAt: "soon" })]])(
    "treats %s as nothing waiting",
    (_name, raw) => {
      const storage = new MemoryStorage();
      storage.items.set(OAUTH_STORAGE_KEY, raw);
      expect(takePending(storage, NOW)).toEqual({ kind: "missing" });
      expect(storage.items.has(OAUTH_STORAGE_KEY)).toBe(false);
    },
  );
});

describe("reading the address on return", () => {
  it("finds the code and state and leaves every other parameter", () => {
    const found = extractReturn("?capability=job_scoring&code=abc12345&state=xyz");
    expect(found?.params).toEqual({ code: "abc12345", state: "xyz", error: null });
    expect(found?.remainingSearch).toBe("?capability=job_scoring");
  });

  it("leaves an empty query string when nothing else was on it", () => {
    expect(extractReturn("?code=abc12345&state=xyz")?.remainingSearch).toBe("");
  });

  it("is null for a visit that is not a return", () => {
    expect(extractReturn("")).toBeNull();
    expect(extractReturn("?capability=job_scoring")).toBeNull();
  });

  it("reads a refusal with no code", () => {
    expect(extractReturn("?error=access_denied&state=xyz")?.params).toEqual({ code: null, state: "xyz", error: "access_denied" });
  });
});

describe("matching a return to the start", () => {
  function waiting(expiresAt = NOW + 1000) {
    const storage = new MemoryStorage();
    savePending(storage, { verifier: "verifier-".padEnd(50, "x"), state: "state-1", expiresAt });
    return storage;
  }

  it("hands over the code and the verifier when the state matches", () => {
    expect(resolveReturn({ code: "the-code", state: "state-1", error: null }, waiting(), NOW)).toEqual({
      kind: "ready",
      code: "the-code",
      verifier: "verifier-".padEnd(50, "x"),
      expiresAt: NOW + 1000,
    });
  });

  it("refuses a state that is not the one it made, and still removes what it kept", () => {
    const storage = waiting();
    expect(resolveReturn({ code: "the-code", state: "state-2", error: null }, storage, NOW)).toEqual({ kind: "failed", reason: "state_mismatch" });
    expect(storage.items.has(OAUTH_STORAGE_KEY)).toBe(false);
  });

  it("refuses a return with no state at all", () => {
    expect(resolveReturn({ code: "the-code", state: null, error: null }, waiting(), NOW)).toEqual({ kind: "failed", reason: "state_mismatch" });
  });

  it("says the ten minutes ran out", () => {
    expect(resolveReturn({ code: "the-code", state: "state-1", error: null }, waiting(NOW - 1), NOW)).toEqual({ kind: "failed", reason: "expired" });
  });

  it("says nothing was waiting (another tab, or a link this flow did not make)", () => {
    expect(resolveReturn({ code: "the-code", state: "state-1", error: null }, new MemoryStorage(), NOW)).toEqual({ kind: "failed", reason: "no_pending" });
    expect(resolveReturn({ code: "the-code", state: "state-1", error: null }, null, NOW)).toEqual({ kind: "failed", reason: "no_pending" });
  });

  it("reads Deny as denied, with or without the state (OpenRouter sends none with a denial)", () => {
    expect(resolveReturn({ code: null, state: "state-1", error: "access_denied" }, waiting(), NOW)).toEqual({ kind: "failed", reason: "denied" });
    expect(resolveReturn({ code: null, state: null, error: "access_denied" }, waiting(), NOW)).toEqual({ kind: "failed", reason: "denied" });
    expect(resolveReturn({ code: null, state: null, error: "access_denied" }, new MemoryStorage(), NOW)).toEqual({ kind: "failed", reason: "denied" });
    expect(resolveReturn({ code: null, state: null, error: "access_denied" }, waiting(NOW - 1), NOW)).toEqual({ kind: "failed", reason: "denied" });
  });

  it("still removes what it kept when the return was a denial", () => {
    const storage = waiting();
    resolveReturn({ code: null, state: null, error: "access_denied" }, storage, NOW);
    expect(storage.items.has(OAUTH_STORAGE_KEY)).toBe(false);
  });

  it("keeps state_mismatch for a code that came back with the wrong state, even with an error beside it", () => {
    expect(resolveReturn({ code: "the-code", state: "state-2", error: "x" }, waiting(), NOW)).toEqual({ kind: "failed", reason: "state_mismatch" });
  });

  it("says a matching state with no code is incomplete", () => {
    expect(resolveReturn({ code: null, state: "state-1", error: null }, waiting(), NOW)).toEqual({ kind: "failed", reason: "no_code" });
  });
});

describe("the return, in order", () => {
  function browser(search: string, storage: MemoryStorage, pathname = "/settings", hash = ""): { b: BrowserLike; calls: string[] } {
    const calls: string[] = [];
    const original = storage.getItem.bind(storage);
    storage.getItem = (key) => {
      calls.push("storage read");
      return original(key);
    };
    return {
      calls,
      b: {
        location: { search, pathname, hash },
        history: {
          state: { idx: 3 },
          replaceState: (_data, _unused, url) => {
            calls.push(`replaceState ${String(url)}`);
          },
        },
      },
    };
  }

  it("takes the parameters off the address BEFORE it reads the stored state or compares anything", () => {
    const storage = new MemoryStorage();
    savePending(storage, { verifier: "v".repeat(50), state: "state-1", expiresAt: NOW + 5000 });
    storage.log.length = 0;
    const { b, calls } = browser("?code=abcdefgh&state=state-1", storage);
    const outcome = captureReturn(b, NOW, storage);
    expect(calls[0]).toBe("replaceState /settings");
    expect(calls.indexOf("storage read")).toBeGreaterThan(0);
    expect(outcome).toMatchObject({ kind: "ready", code: "abcdefgh" });
  });

  it("reads a Deny that comes back with no state as a denial, with the address cleaned either way", () => {
    const storage = new MemoryStorage();
    savePending(storage, { verifier: "v".repeat(50), state: "state-1", expiresAt: NOW + 5000 });
    const { b, calls } = browser("?error=access_denied&error_description=The+user+denied.", storage);
    expect(captureReturn(b, NOW, storage)).toEqual({ kind: "failed", reason: "denied" });
    expect(calls[0]).toBe("replaceState /settings");
    expect(storage.items.has(OAUTH_STORAGE_KEY)).toBe(false);
  });

  it("keeps the other query parameters and the fragment when it rewrites the address", () => {
    const { b, calls } = browser("?capability=job_scoring&code=abcdefgh&state=s", new MemoryStorage(), "/settings", "#ai-provider");
    captureReturn(b, NOW, new MemoryStorage());
    expect(calls[0]).toBe("replaceState /settings?capability=job_scoring#ai-provider");
  });

  it("removes the address parameters even when the return then fails", () => {
    const { b, calls } = browser("?code=abcdefgh&state=wrong", new MemoryStorage());
    expect(captureReturn(b, NOW, new MemoryStorage())).toEqual({ kind: "failed", reason: "no_pending" });
    expect(calls[0]).toBe("replaceState /settings");
  });

  it("does nothing on another page, or on a settings visit that is not a return", () => {
    for (const [search, pathname] of [["?error=access_denied", "/"], ["?code=abcdefgh&state=s", "/discover"], ["", "/settings"], ["?capability=x", "/settings"]] as const) {
      const { b, calls } = browser(search, new MemoryStorage(), pathname);
      expect(captureReturn(b, NOW, new MemoryStorage())).toBeNull();
      expect(calls).toEqual([]);
    }
  });

  it("is the first import of main.tsx, ahead of anything that reads the address", () => {
    const imports = [...mainSource.matchAll(/^import .*$/gm)].map((m) => m[0]);
    expect(imports[0]).toContain("captureOAuthReturn");
  });

  it("writes nothing to the console", () => {
    for (const source of [oauthSource, captureSource]) expect(codeOnly(source)).not.toMatch(/console\./);
  });
});

describe("the exchange store, and how long a code may wait", () => {
  it("never sends a code that waited past the sign-in's ten minutes, and says it ran out", () => {
    let now = NOW;
    const store = createConnectStore<string>(() => now);
    const exchange = vi.fn(async () => "row");
    store.receive({ kind: "ready", code: "the-code", verifier: "the-verifier", expiresAt: NOW + 5000 });
    now = NOW + 5000;
    store.begin(exchange, () => false);
    expect(exchange).not.toHaveBeenCalled();
    expect(store.getSnapshot()).toEqual({ phase: "failed", failure: { kind: "expired" } });
    // and it is gone: nothing can start it later
    store.acknowledge();
    store.begin(exchange, () => false);
    expect(exchange).not.toHaveBeenCalled();
  });

  it("drops a waiting code when the person signs out, so whoever signs in next cannot spend it", () => {
    const store = createConnectStore<string>(() => NOW);
    const exchange = vi.fn(async () => "row");
    store.receive({ kind: "ready", code: "the-code", verifier: "the-verifier", expiresAt: NOW + 5000 });
    store.acknowledge();
    store.begin(exchange, () => false);
    expect(exchange).not.toHaveBeenCalled();
    expect(store.getSnapshot()).toEqual({ phase: "idle" });
  });

  it("does not repeat an exchange after the ten minutes either", async () => {
    let now = NOW;
    const store = createConnectStore<string>(() => now);
    const exchange = vi.fn(async () => {
      throw new Error("busy");
    });
    store.receive({ kind: "ready", code: "the-code", verifier: "the-verifier", expiresAt: NOW + 5000 });
    store.begin(exchange, () => true);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(store.getSnapshot()).toMatchObject({ phase: "failed", failure: { kind: "retryable" } });
    now = NOW + 6000;
    store.retry();
    expect(exchange).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toEqual({ phase: "failed", failure: { kind: "expired" } });
  });
});

describe("the exchange store", () => {
  const ready: ReturnOutcome = { kind: "ready", code: "the-code", verifier: "the-verifier", expiresAt: NOW + 5000 };
  const never = () => false;
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

  it("runs nothing when nothing came back", async () => {
    const store = createConnectStore<string>(() => NOW);
    const exchange = vi.fn();
    store.receive(null);
    store.begin(exchange, never);
    expect(exchange).not.toHaveBeenCalled();
    expect(store.getSnapshot()).toEqual({ phase: "idle" });
  });

  it("sends the code and the verifier to the exchange, once, however many times the page asks", async () => {
    const store = createConnectStore<string>(() => NOW);
    const exchange = vi.fn(async () => "row");
    store.receive(ready);
    store.begin(exchange, never);
    store.begin(exchange, never);
    expect(exchange).toHaveBeenCalledTimes(1);
    expect(exchange).toHaveBeenCalledWith("the-code", "the-verifier");
    await settle();
    expect(store.getSnapshot()).toEqual({ phase: "connected", credential: "row" });
  });

  it("tells subscribers as it goes", async () => {
    const store = createConnectStore<string>(() => NOW);
    const phases: string[] = [];
    store.subscribe(() => phases.push(store.getSnapshot().phase));
    store.receive(ready);
    store.begin(async () => "row", never);
    await settle();
    expect(phases).toEqual(["idle", "exchanging", "connected"]);
  });

  it("reports a refused code as final, and forgets the code and verifier", async () => {
    const store = createConnectStore<string>(() => NOW);
    const exchange = vi.fn(async () => {
      throw new Error("refused");
    });
    store.receive(ready);
    store.begin(exchange, never);
    await settle();
    expect(store.getSnapshot()).toMatchObject({ phase: "failed", failure: { kind: "rejected" } });
    store.retry();
    expect(exchange).toHaveBeenCalledTimes(1);
  });

  it("keeps the code for a failure that may pass, and retries from memory", async () => {
    const store = createConnectStore<string>(() => NOW);
    let attempts = 0;
    const exchange = vi.fn(async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("offline");
      return "row";
    });
    store.receive(ready);
    store.begin(exchange, () => true);
    await settle();
    expect(store.getSnapshot()).toMatchObject({ phase: "failed", failure: { kind: "retryable" } });
    store.retry();
    await settle();
    expect(exchange).toHaveBeenLastCalledWith("the-code", "the-verifier");
    expect(store.getSnapshot()).toEqual({ phase: "connected", credential: "row" });
  });

  it.each(["denied", "state_mismatch", "expired", "no_pending", "no_code"] as const)("carries a %s return as a failure with nothing to retry", (reason) => {
    const store = createConnectStore<string>(() => NOW);
    store.receive({ kind: "failed", reason });
    const exchange = vi.fn();
    store.begin(exchange, never);
    expect(store.getSnapshot()).toEqual({ phase: "failed", failure: { kind: reason } });
    store.retry();
    expect(exchange).not.toHaveBeenCalled();
  });

  it("goes back to idle once the person has seen the result, but not in the middle of an exchange", async () => {
    const store = createConnectStore<string>(() => NOW);
    let finish: (value: string) => void = () => undefined;
    store.receive(ready);
    store.begin(() => new Promise<string>((resolve) => (finish = resolve)), never);
    store.acknowledge();
    expect(store.getSnapshot().phase).toBe("exchanging");
    finish("row");
    await settle();
    store.acknowledge();
    expect((store.getSnapshot() as ConnectState<string>).phase).toBe("idle");
  });
});
