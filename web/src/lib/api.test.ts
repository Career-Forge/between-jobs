import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// api.ts reads the session from the Supabase client; this test has no session to
// get and nothing to connect to, so it is replaced with a signed-in stand-in.
vi.mock("./supabase", () => ({
  supabase: {
    auth: { getSession: async () => ({ data: { session: { access_token: "test-token" } } }) },
  },
}));

import { ApiError, apiFetch, apiFetchBlob } from "./api";

function envelope(error: Record<string, unknown>): string {
  return JSON.stringify({ error: { capability: null, missing: null, ...error } });
}

function stubFetch(status: number, body: string | null, headers: Record<string, string> = {}) {
  const fetchMock = vi.fn(async () => new Response(body, { status, headers }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// The error a call rejects with, as an ApiError (the test fails if it is anything else).
async function failure(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error("the call was expected to fail");
}

describe("a 429 RATE_LIMITED reply", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("becomes an ApiError with the code, retryable, and the wait from the envelope", async () => {
    stubFetch(
      429,
      envelope({
        code: "RATE_LIMITED",
        message: "You've reached the limit for this action (10 per hour). Try again in about 13 minutes.",
        retryable: true,
        details: { retry_after_seconds: 725, bucket: "prepare" },
      }),
      { "Retry-After": "725" },
    );

    const error = await failure(apiFetch("/applications/x/prepare", { method: "POST" }));

    expect(error.status).toBe(429);
    expect(error.code).toBe("RATE_LIMITED");
    expect(error.retryable).toBe(true);
    expect(error.retryAfterSeconds).toBe(725);
  });

  it("falls back to the Retry-After header when the body has no details", async () => {
    stubFetch(429, "not json at all", { "Retry-After": "90" });

    const error = await failure(apiFetch("/discover"));

    expect(error.code).toBeUndefined();
    expect(error.retryAfterSeconds).toBe(90);
  });

  it("is the same through the PDF download path", async () => {
    stubFetch(
      429,
      envelope({ code: "RATE_LIMITED", message: "x", retryable: true, details: { retry_after_seconds: 30 } }),
    );

    const error = await failure(apiFetchBlob("/applications/x/resume.pdf"));

    expect(error.code).toBe("RATE_LIMITED");
    expect(error.retryable).toBe(true);
    expect(error.retryAfterSeconds).toBe(30);
  });
});

describe("other error replies", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("a 413 PAYLOAD_TOO_LARGE keeps its code and is not retryable", async () => {
    stubFetch(
      413,
      envelope({ code: "PAYLOAD_TOO_LARGE", message: "too big", retryable: false, details: { max_bytes: 1048576 } }),
    );

    const error = await failure(apiFetch("/profile/versions", { method: "POST", body: "{}" }));

    expect(error.status).toBe(413);
    expect(error.code).toBe("PAYLOAD_TOO_LARGE");
    expect(error.retryable).toBe(false);
    expect(error.retryAfterSeconds).toBeUndefined();
  });

  it("an ordinary error carries no wait", async () => {
    stubFetch(409, envelope({ code: "SETUP_REQUIRED", message: "Add a key.", retryable: false, details: {} }));

    const error = await failure(apiFetch("/discover"));

    expect(error.code).toBe("SETUP_REQUIRED");
    expect(error.retryAfterSeconds).toBeUndefined();
  });

  it("a success is still the parsed body, and a 204 is still undefined", async () => {
    stubFetch(200, JSON.stringify({ ok: true }));
    expect(await apiFetch("/x")).toEqual({ ok: true });
    stubFetch(204, null);
    expect(await apiFetch("/x")).toBeUndefined();
  });
});
