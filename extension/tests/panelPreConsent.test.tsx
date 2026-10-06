import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONSENT_STORAGE_KEY, CONSENT_VERSION } from "@/lib/consent";

// The side panel with the REAL Supabase client (tests/App.test.tsx uses a fake). The claim under
// test is the privacy policy's: until the person has agreed to the disclosure, the extension
// "sends nothing anywhere". Constructing a real client is itself a network event when the stored
// sign-in session has expired -- the library starts its auth listener, which refreshes an expired
// token with the sign-in service and sends the refresh token -- so the panel must not construct it
// before agreement. The fake client in App.test.tsx cannot show that; this file can.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
const fetchCalls: Array<{ url: string; method: string; body: string | null }> = [];

function sessionJson(expiresAtSeconds: number): string {
  return JSON.stringify({
    access_token: "old-access",
    refresh_token: "old-refresh",
    token_type: "bearer",
    expires_in: 3600,
    expires_at: expiresAtSeconds,
    user: { id: "user-1", email: "a@example.com" },
  });
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

beforeEach(() => {
  vi.resetModules();
  fetchCalls.length = 0;
  vi.stubEnv("WXT_SUPABASE_URL", "https://abcdefgh.supabase.co");
  vi.stubEnv("WXT_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test");
  vi.stubEnv("WXT_API_BASE_URL", "https://api.example.test");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function installGlobals(options: { consent: unknown; expiresAt: number }) {
  const local: Record<string, unknown> = {};
  if (options.consent !== undefined) local[CONSENT_STORAGE_KEY] = options.consent;
  const session: Record<string, unknown> = { "sb-abcdefgh-auth-token": sessionJson(options.expiresAt) };
  vi.stubGlobal("chrome", {
    storage: {
      local: {
        get: async (key: string) => (key in local ? { [key]: local[key] } : {}),
        set: async (values: Record<string, unknown>) => Object.assign(local, values),
      },
      session: {
        get: async (key: string) => (key in session ? { [key]: session[key] } : {}),
        set: async (values: Record<string, unknown>) => Object.assign(session, values),
        remove: async (key: string) => delete session[key],
      },
      onChanged: { addListener: vi.fn(), removeListener: vi.fn() },
    },
  });
  vi.stubGlobal("browser", {
    tabs: {
      query: vi.fn(async () => [{ id: 1 }]),
      sendMessage: vi.fn(async () => null),
      onUpdated: { addListener: vi.fn(), removeListener: vi.fn() },
      onActivated: { addListener: vi.fn(), removeListener: vi.fn() },
    },
    runtime: {
      sendMessage: vi.fn(async () => null),
      onMessage: { addListener: vi.fn(), removeListener: vi.fn() },
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      fetchCalls.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? init.body : null,
      });
      return new Response(
        JSON.stringify({
          access_token: "new-access",
          refresh_token: "new-refresh",
          token_type: "bearer",
          expires_in: 3600,
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          user: { id: "user-1", email: "a@example.com" },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }),
  );
}

async function mountPanel(): Promise<void> {
  const { default: App } = await import("@/entrypoints/sidepanel/App");
  await act(async () => {
    root.render(<App />);
  });
  await flush();
}

const EXPIRED = () => Math.floor(Date.now() / 1000) - 3600;
const VALID = () => Math.floor(Date.now() / 1000) + 3000;
const refreshCalls = () => fetchCalls.filter((c) => c.url.includes("/auth/v1/token") && c.url.includes("refresh_token"));

describe("the side panel before the person has agreed, with the real sign-in client", () => {
  it("an expired stored session and no consent flag: the gate is shown and nothing is sent anywhere", async () => {
    installGlobals({ consent: undefined, expiresAt: EXPIRED() });

    await mountPanel();

    expect(container.textContent).toContain("Before you sign in");
    expect(fetchCalls).toEqual([]);
  });

  it("a stale stored consent version is no consent either", async () => {
    installGlobals({ consent: { version: CONSENT_VERSION - 1 }, expiresAt: EXPIRED() });

    await mountPanel();

    expect(container.textContent).toContain("Before you sign in");
    expect(fetchCalls).toEqual([]);
  });

  it("agreeing is what lets the session refresh, once", async () => {
    installGlobals({ consent: undefined, expiresAt: EXPIRED() });
    await mountPanel();
    expect(refreshCalls()).toEqual([]);

    const agree = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("I understand and agree"));
    await act(async () => {
      agree!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();

    expect(refreshCalls()).toHaveLength(1);
    expect(refreshCalls()[0]!.body).toContain("old-refresh");
    expect(container.textContent).not.toContain("Before you sign in");
  });

  it("control: with consent already given, an expired session is refreshed", async () => {
    installGlobals({ consent: { version: CONSENT_VERSION }, expiresAt: EXPIRED() });

    await mountPanel();

    expect(refreshCalls()).toHaveLength(1);
  });

  it("a session that has not expired needs no request, with or without consent", async () => {
    installGlobals({ consent: undefined, expiresAt: VALID() });
    await mountPanel();
    expect(fetchCalls).toEqual([]);
  });
});
