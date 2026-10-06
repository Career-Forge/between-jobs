import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// api.ts reads the session from the Supabase client; this test has no session to
// get and nothing to connect to, so it is replaced with a signed-in stand-in.
vi.mock("./supabase", () => ({
  supabase: {
    auth: { getSession: async () => ({ data: { session: { access_token: "test-token" } } }) },
  },
}));

import { ApiError, apiFetch, apiFetchBlob, setEnrollmentRefusalListener } from "./api";

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

describe("a 409 SETUP_REQUIRED reply", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("carries where to fix it and what is missing, from the envelope", async () => {
    stubFetch(
      409,
      envelope({
        code: "SETUP_REQUIRED",
        message: "No openrouter key configured for 'job_scoring'.",
        retryable: false,
        capability: "job_scoring",
        missing: ["credential"],
        settings_path: "/profile/integrations?capability=job_scoring",
        details: {},
      }),
    );

    const error = await failure(apiFetch("/discover"));

    expect(error.code).toBe("SETUP_REQUIRED");
    expect(error.settingsPath).toBe("/profile/integrations?capability=job_scoring");
    expect(error.capability).toBe("job_scoring");
    expect(error.missing).toEqual(["credential"]);
  });

  it("is the same through the PDF download path", async () => {
    stubFetch(
      409,
      envelope({ code: "SETUP_REQUIRED", message: "x", settings_path: "/profile", capability: "profile" }),
    );

    const error = await failure(apiFetchBlob("/applications/x/resume.pdf"));

    expect(error.settingsPath).toBe("/profile");
    expect(error.capability).toBe("profile");
  });

  it("reads a field of the wrong type as absent, and the nulls of an ordinary error as absent", async () => {
    stubFetch(
      409,
      envelope({ code: "SETUP_REQUIRED", message: "x", settings_path: 12, capability: ["a"], missing: "credential" }),
    );
    const odd = await failure(apiFetch("/x"));
    expect(odd.settingsPath).toBeUndefined();
    expect(odd.capability).toBeUndefined();
    expect(odd.missing).toBeUndefined();

    stubFetch(404, envelope({ code: "NOT_FOUND", message: "gone", settings_path: null }));
    const ordinary = await failure(apiFetch("/x"));
    expect(ordinary.settingsPath).toBeUndefined();
    expect(ordinary.capability).toBeUndefined();
    expect(ordinary.missing).toBeUndefined();
  });

  it("has none of them when the body is not the envelope at all", async () => {
    stubFetch(409, "<html>bad gateway</html>");
    const error = await failure(apiFetch("/x"));
    expect(error.settingsPath).toBeUndefined();
    expect(error.capability).toBeUndefined();
    expect(error.missing).toBeUndefined();
  });
});

// A server that requires the tester programme refuses the costly features to someone who has not
// joined, and a tab that was enrolled when it opened can be refused later (a withdrawal in another
// tab, a newer agreement, the programme switched on). The shell's gate listens, asks where the
// person stands again, and puts the enrollment page where the feature was.
describe("a 403 ENROLLMENT_REQUIRED reply", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => {
    vi.unstubAllGlobals();
    setEnrollmentRefusalListener(null);
  });

  const refusal = () =>
    envelope({
      code: "ENROLLMENT_REQUIRED",
      message: "Joining the tester programme comes first. Open the Between Jobs website, accept the tester agreement there, then try again.",
      retryable: false,
    });

  it("tells the listener, and still throws the error, with its code, for the page that asked to show", async () => {
    const listener = vi.fn();
    setEnrollmentRefusalListener(listener);
    stubFetch(403, refusal());

    const error = await failure(apiFetch("/applications/x/prepare", { method: "POST" }));

    expect(listener).toHaveBeenCalledTimes(1);
    expect(error.status).toBe(403);
    expect(error.code).toBe("ENROLLMENT_REQUIRED");
    expect(error.retryable).toBe(false);
  });

  it("does the same through the PDF download path", async () => {
    const listener = vi.fn();
    setEnrollmentRefusalListener(listener);
    stubFetch(403, refusal());

    const error = await failure(apiFetchBlob("/applications/x/resume.pdf"));

    expect(listener).toHaveBeenCalledTimes(1);
    expect(error.code).toBe("ENROLLMENT_REQUIRED");
  });

  it("is not announced for any other failure, not even another 403", async () => {
    const listener = vi.fn();
    setEnrollmentRefusalListener(listener);

    stubFetch(403, envelope({ code: "FORBIDDEN", message: "No." }));
    await failure(apiFetch("/x"));
    stubFetch(403, "not json");
    await failure(apiFetch("/x"));
    stubFetch(429, envelope({ code: "RATE_LIMITED", message: "x", retryable: true }));
    await failure(apiFetch("/x"));
    stubFetch(409, envelope({ code: "SETUP_REQUIRED", message: "x" }));
    await failure(apiFetch("/x"));
    stubFetch(200, JSON.stringify({ ok: true }));
    await apiFetch("/x");

    expect(listener).not.toHaveBeenCalled();
  });

  it("throws the same error when nothing is listening, and when the listener itself fails", async () => {
    stubFetch(403, refusal());
    expect((await failure(apiFetch("/x"))).code).toBe("ENROLLMENT_REQUIRED");

    setEnrollmentRefusalListener(() => {
      throw new Error("the listener broke");
    });
    expect((await failure(apiFetch("/x"))).code).toBe("ENROLLMENT_REQUIRED");
  });

  it("goes to the listener that is registered now, and to none once it is cleared", async () => {
    const first = vi.fn();
    const second = vi.fn();
    stubFetch(403, refusal());

    setEnrollmentRefusalListener(first);
    setEnrollmentRefusalListener(second);
    await failure(apiFetch("/x"));
    setEnrollmentRefusalListener(null);
    await failure(apiFetch("/x"));

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
