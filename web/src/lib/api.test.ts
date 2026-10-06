import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// api.ts reads the session from the Supabase client; this test has no session to
// get and nothing to connect to, so it is replaced with a signed-in stand-in.
vi.mock("./supabase", () => ({
  supabase: {
    auth: { getSession: async () => ({ data: { session: { access_token: "test-token" } } }) },
  },
}));

import {
  ApiError,
  apiFetch,
  apiFetchBlob,
  apiFetchBytes,
  setEnrollmentRefusalListener,
} from "./api";
import {
  SERVER_DID_NOT_FINISH_MESSAGE,
  UPLOAD_FAILED,
  discardDraft,
  importFailureOf,
  type ImportEvent,
} from "./resumeImport";

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

// A body of raw bytes (the resume file import): the same token, the same base and the same error
// envelope as every other call, with the caller's own Content-Type instead of JSON.
describe("apiFetchBytes", () => {
  afterEach(() => vi.unstubAllGlobals());

  function sentRequest(fetchMock: ReturnType<typeof stubFetch>): { url: string; init: RequestInit } {
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    return { url, init };
  }

  it("posts the bytes as they are, with the given Content-Type and the person's token", async () => {
    const fetchMock = stubFetch(201, JSON.stringify({ version_id: "v1" }));
    const bytes = new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])]);

    const answer = await apiFetchBytes<{ version_id: string }>(
      "/profile/import-document?filename=resume.pdf",
      bytes,
      "application/pdf",
    );

    expect(answer).toEqual({ version_id: "v1" });
    const { url, init } = sentRequest(fetchMock);
    expect(url).toMatch(/\/profile\/import-document\?filename=resume\.pdf$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(bytes);
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/pdf");
    expect(headers.Authorization).toBe("Bearer test-token");
  });

  it("does not leave the JSON Content-Type on a body that is not JSON", async () => {
    const fetchMock = stubFetch(201, "{}");
    await apiFetchBytes("/x", new Blob(["a"]), "application/octet-stream");
    const headers = sentRequest(fetchMock).init.headers as Record<string, string>;
    expect(Object.values(headers).filter((value) => value === "application/json")).toEqual([]);
  });

  it("fails with the same ApiError the JSON calls give: code, status, retryable, wait", async () => {
    stubFetch(
      429,
      envelope({
        code: "RATE_LIMITED",
        message: "Several resume files are being read right now. Try again in a few seconds.",
        retryable: true,
        details: { retry_after_seconds: 5 },
      }),
      { "Retry-After": "5" },
    );
    const error = await failure(apiFetchBytes("/profile/import-document", new Blob(["x"]), "application/pdf"));
    expect(error.status).toBe(429);
    expect(error.code).toBe("RATE_LIMITED");
    expect(error.retryable).toBe(true);
    expect(error.retryAfterSeconds).toBe(5);
  });

  it("keeps a 415 and a 422 with their codes and the server's own sentences", async () => {
    stubFetch(415, envelope({ code: "UNSUPPORTED_MEDIA_TYPE", message: "That file is not a PDF or a DOCX.", retryable: false }));
    const unsupported = await failure(apiFetchBytes("/x", new Blob(["x"]), "application/pdf"));
    expect(unsupported.status).toBe(415);
    expect(unsupported.code).toBe("UNSUPPORTED_MEDIA_TYPE");
    expect(unsupported.message).toBe("That file is not a PDF or a DOCX.");

    stubFetch(422, envelope({ code: "INVALID_INPUT", message: "That PDF is encrypted.", retryable: false }));
    const invalid = await failure(apiFetchBytes("/x", new Blob(["x"]), "application/pdf"));
    expect(invalid.status).toBe(422);
    expect(invalid.message).toBe("That PDF is encrypted.");
  });

  it("is refused with a 401 when nobody is signed in, before anything is sent", async () => {
    vi.resetModules();
    vi.doMock("./supabase", () => ({
      supabase: { auth: { getSession: async () => ({ data: { session: null } }) } },
    }));
    const fetchMock = stubFetch(201, "{}");
    const fresh = await import("./api");
    try {
      await fresh.apiFetchBytes("/x", new Blob(["x"]), "application/pdf");
      throw new Error("expected a failure");
    } catch (e) {
      expect(e).toBeInstanceOf(fresh.ApiError);
      expect((e as { status: number }).status).toBe(401);
    }
    expect(fetchMock).not.toHaveBeenCalled();
    vi.doUnmock("./supabase");
  });
});

// Every JSON call goes through the one sender that the raw-bytes call shares, which is told the
// request body's Content-Type: the JSON calls must still send JSON (a server that is sent anything
// else does not parse the body), and a caller's own headers still win.
describe("apiFetch request headers", () => {
  afterEach(() => vi.unstubAllGlobals());

  function headersSent(fetchMock: ReturnType<typeof stubFetch>): Record<string, string> {
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    return init.headers as Record<string, string>;
  }

  it("sends a JSON body as application/json, with the person's token", async () => {
    const fetchMock = stubFetch(200, "{}");
    await apiFetch("/x", { method: "POST", body: "{}" });
    const headers = headersSent(fetchMock);
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers.Authorization).toBe("Bearer test-token");
  });

  it("lets a caller's own headers add to the defaults and win over them", async () => {
    const fetchMock = stubFetch(200, "{}");
    await apiFetch("/y", { method: "POST", body: "{}", headers: { "X-Test": "1", "Content-Type": "text/csv" } });
    const headers = headersSent(fetchMock);
    expect(headers["X-Test"]).toBe("1");
    expect(headers["Content-Type"]).toBe("text/csv");
    expect(headers.Authorization).toBe("Bearer test-token");
  });

  it("apiFetchBytes keeps the given type even when the shared sender defaults to JSON", async () => {
    const fetchMock = stubFetch(201, "{}");
    await apiFetchBytes("/x", new Blob(["a"]), "application/pdf");
    expect(headersSent(fetchMock)["Content-Type"]).toBe("application/pdf");
  });
});

// `details.reason` is how a client tells two 429s apart that the code does not: the server's own
// capacity (every resume reader busy) from the person's own count.
describe("an error reply's details.reason", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is carried onto the ApiError when it is text", async () => {
    stubFetch(
      429,
      envelope({
        code: "RATE_LIMITED",
        message: "Several resume files are being read right now. Try again in a few seconds.",
        retryable: true,
        details: { retry_after_seconds: 5, reason: "readers_busy" },
      }),
      { "Retry-After": "5" },
    );
    const error = await failure(apiFetchBytes("/profile/import-document", new Blob(["x"]), "application/pdf"));
    expect(error.code).toBe("RATE_LIMITED");
    expect(error.reason).toBe("readers_busy");
    expect(error.retryAfterSeconds).toBe(5);
  });

  it("is absent on the per-user limiter's reply, which names its bucket instead", async () => {
    stubFetch(429, envelope({ code: "RATE_LIMITED", message: "x", retryable: true, details: { retry_after_seconds: 1500, bucket: "profile_import" } }));
    const error = await failure(apiFetchBytes("/profile/import-document", new Blob(["x"]), "application/pdf"));
    expect(error.reason).toBeUndefined();
  });

  it("is ignored when it is not text, or empty, or the details are not an object", async () => {
    for (const details of [{ reason: 5 }, { reason: "" }, { reason: null }, { reason: ["readers_busy"] }, "readers_busy", null, 7]) {
      stubFetch(429, envelope({ code: "RATE_LIMITED", message: "x", retryable: true, details }));
      const error = await failure(apiFetch("/x"));
      expect(error.reason, JSON.stringify(details)).toBeUndefined();
    }
  });

  it("is absent on a reply that is not the API's envelope", async () => {
    stubFetch(429, "<html>Too Many Requests</html>");
    expect((await failure(apiFetch("/x"))).reason).toBeUndefined();
  });
});

// What the resume import makes of replies from an edge in front of the API, through the real client:
// a gateway timeout is not an application error, and a 404 that is not the API's own is not "the
// draft is gone".
describe("the resume import, through the real client, on replies that are not the API's", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("a gateway reply with an HTML body is asked again, and says the server did not finish", async () => {
    for (const status of [502, 503, 504]) {
      stubFetch(status, "<html><body>Bad Gateway</body></html>", { "Content-Type": "text/html" });
      const error = await failure(apiFetchBytes("/profile/import-document", new Blob(["x"]), "application/pdf"));
      expect(error.code).toBeUndefined();
      expect(importFailureOf(error, UPLOAD_FAILED)).toEqual({
        failure: { kind: "error", message: SERVER_DID_NOT_FINISH_MESSAGE },
        retry: true,
      });
    }
  });

  it("a 404 with an HTML body, on the discard request, is a failure to discard and not a discarded draft", async () => {
    stubFetch(404, "<html>Not Found</html>", { "Content-Type": "text/html" });
    const events: ImportEvent[] = [];
    await discardDraft(
      "v1",
      {
        upload: async () => undefined,
        activate: async () => undefined,
        discard: (id) => apiFetch(`/profile/versions/${id}`, { method: "DELETE" }),
      },
      (event) => events.push(event),
    );
    expect(events.map((event) => event.type)).toEqual(["discard_started", "discard_failed"]);
  });

  it("the API's own 404 NOT_FOUND, on the discard request, is a draft that is already gone", async () => {
    stubFetch(404, envelope({ code: "NOT_FOUND", message: "no profile version found for id 'v1'", retryable: false }));
    const events: ImportEvent[] = [];
    await discardDraft(
      "v1",
      {
        upload: async () => undefined,
        activate: async () => undefined,
        discard: (id) => apiFetch(`/profile/versions/${id}`, { method: "DELETE" }),
      },
      (event) => events.push(event),
    );
    expect(events).toEqual([{ type: "discard_started" }, { type: "discarded", keptInHistory: false }]);
  });
});
