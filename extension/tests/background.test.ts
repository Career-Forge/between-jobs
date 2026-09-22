import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { SignedFieldMapResponse } from "@/lib/ats-field-map";
import type { FetchApplicationFilesResult, TabState } from "@/lib/types";

// Drives the real background.ts through its real onMessage listener, with the
// network (apiFetch), the Supabase session and chrome.storage stubbed. The
// field-map signature check is NOT stubbed: these use a freshly generated
// Ed25519 key registered as trusted, so what's under test is the actual
// verification-then-binding-then-ratchet path.

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  apiFetchBlob: vi.fn(),
  getSession: vi.fn(),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public readonly status: number,
      message: string,
      public readonly code?: string,
    ) {
      super(message);
    }
  }
  return { ApiError, apiFetch: mocks.apiFetch, apiFetchBlob: mocks.apiFetchBlob };
});
vi.mock("@/lib/supabase", () => ({
  getSupabaseClient: () => ({ auth: { getSession: mocks.getSession } }),
}));

const TEST_KEY_ID = "test-key-e6";
const APP_ID = "0b7f3c1e-5d2a-4e8b-9c41-2f6a8d9e0b13";
const EXTENSION_ID = "test-extension";

let privateKey: CryptoKey;
let publicKeyB64: string;

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

beforeAll(async () => {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  privateKey = pair.privateKey;
  publicKeyB64 = toBase64(new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey)));
});

async function signedMap(
  atsType: "lever" | "greenhouse" | "ashby",
  version: number,
): Promise<SignedFieldMapResponse> {
  const payload: Record<string, unknown> = {
    ats_type: atsType,
    version,
    schema: "ats-field-map/v1",
    standard_fields: [],
  };
  if (atsType === "lever") {
    Object.assign(payload, {
      custom_question_prefix: "cards[",
      label_wrapper_selector: ".application-field",
      label_selector: ".application-label",
      cover_letter_label_pattern: "cover letter",
    });
  }
  const payloadCanonical = JSON.stringify(payload);
  const signature = await crypto.subtle.sign({ name: "Ed25519" }, privateKey, new TextEncoder().encode(payloadCanonical));
  return {
    ats_type: atsType,
    version,
    schema: "ats-field-map/v1",
    payload_canonical: payloadCanonical,
    signature_b64: toBase64(new Uint8Array(signature)),
    signing_key_id: TEST_KEY_ID,
  };
}

const CONTENT_SCRIPT = { id: EXTENSION_ID, url: "https://jobs.lever.co/acme/aaaa-1111/apply", tab: { id: 7 } };
const SIDE_PANEL = { id: EXTENSION_ID, url: `chrome-extension://${EXTENSION_ID}/sidepanel.html` };

type Listener = (message: unknown, sender: unknown) => unknown;

async function loadBackground(options: { storage?: Record<string, unknown>; storageLatencyMs?: number } = {}) {
  vi.resetModules();
  const store: Record<string, unknown> = { ...(options.storage ?? {}) };
  const delay = () => new Promise((resolve) => setTimeout(resolve, options.storageLatencyMs ?? 0));
  vi.stubGlobal("chrome", {
    storage: {
      local: {
        get: async (key: string) => {
          await delay();
          return key in store ? { [key]: store[key] } : {};
        },
        set: async (values: Record<string, unknown>) => {
          await delay();
          Object.assign(store, values);
        },
      },
    },
    sidePanel: { setPanelBehavior: async () => {} },
  });
  const listeners: Listener[] = [];
  vi.stubGlobal("browser", {
    runtime: {
      id: EXTENSION_ID,
      getURL: (path = "") => `chrome-extension://${EXTENSION_ID}/${path}`,
      onMessage: { addListener: (fn: Listener) => listeners.push(fn) },
    },
  });
  vi.stubGlobal("defineBackground", (main: () => void) => ({ main }));

  // Trust the test key in the SAME module instance background.ts will import.
  const fieldMap = await import("@/lib/ats-field-map");
  fieldMap.KNOWN_PUBLIC_KEYS[TEST_KEY_ID] = publicKeyB64;
  const module = await import("@/entrypoints/background");
  (module.default as unknown as { main: () => void }).main();

  const listener = listeners[0];
  if (listener === undefined) throw new Error("background registered no message listener");
  return {
    store,
    send: (message: unknown, sender: unknown = CONTENT_SCRIPT) => listener(message, sender),
  };
}

interface Routes {
  fieldMap?: (atsType: string) => SignedFieldMapResponse | Promise<SignedFieldMapResponse> | Error;
}

function routeApi(routes: Routes = {}): void {
  mocks.apiFetch.mockImplementation(async (path: string) => {
    if (path.startsWith("/extension/lookup")) return { application_id: APP_ID };
    if (path === `/applications/${APP_ID}/extension-payload`) return { prepare_result: null, personal_info: null };
    if (path.startsWith("/extension/field-maps/")) {
      const result = routes.fieldMap ? await routes.fieldMap(path.split("/").pop()!) : undefined;
      if (result === undefined) {
        const { ApiError } = await import("@/lib/api");
        throw new ApiError(404, "not found");
      }
      if (result instanceof Error) throw result;
      return result;
    }
    if (path.endsWith("/stage")) return { status: "applied" };
    if (path === "/extension/match-answer") return { answer: null };
    if (path === "/extension/draft-answer") {
      return { eligible: true, answer_text: "draft", declined_reason: null, warnings: [] };
    }
    return {};
  });
}

function detect(harness: Awaited<ReturnType<typeof loadBackground>>, atsType = "greenhouse", url?: string) {
  const defaultUrl =
    atsType === "greenhouse"
      ? "https://job-boards.greenhouse.io/acme/jobs/1"
      : atsType === "lever"
        ? "https://jobs.lever.co/acme/aaaa-1111/apply"
        : "https://jobs.ashbyhq.com/acme/bbbb-2222/application";
  return harness.send({ type: "PAGE_DETECTED", atsType, url: url ?? defaultUrl }) as Promise<TabState>;
}

beforeEach(() => {
  mocks.apiFetch.mockReset();
  mocks.apiFetchBlob.mockReset();
  mocks.getSession.mockReset();
  mocks.getSession.mockResolvedValue({ data: { session: { user: { id: "user-1" } } } });
  routeApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const apiPaths = () => mocks.apiFetch.mock.calls.map((call) => String(call[0]));

// ---- D4-1: a validly-signed map for the WRONG ats_type ---------------------------

describe("field-map binding is checked against the ATS that was requested", () => {
  it("refuses a validly-signed Lever map served for /greenhouse, with an explanation", async () => {
    const harness = await loadBackground();
    routeApi({ fieldMap: async () => signedMap("lever", 7) });

    const state = await detect(harness, "greenhouse");

    expect(state.status).toBe("tracked");
    if (state.status !== "tracked") return;
    expect(state.fieldMap).toBeNull();
    expect(state.fieldMapError).toMatch(/different ATS/);
  });

  it("doesn't let it poison the version floor: nothing is recorded for either ATS", async () => {
    const harness = await loadBackground();
    routeApi({ fieldMap: async () => signedMap("lever", 7) });

    await detect(harness, "greenhouse");

    expect(harness.store).toEqual({});
  });

  it("so a genuine Greenhouse v1 afterwards is accepted, not refused as 'older'", async () => {
    const harness = await loadBackground();
    routeApi({ fieldMap: async () => signedMap("lever", 7) });
    await detect(harness, "greenhouse");

    routeApi({ fieldMap: async () => signedMap("greenhouse", 1) });
    const state = await detect(harness, "greenhouse");

    expect(state.status === "tracked" && state.fieldMap?.ats_type).toBe("greenhouse");
    expect(harness.store["fieldMapVersion:greenhouse"]).toBe(1);
  });

  it("accepts a correctly-typed map and records its version", async () => {
    const harness = await loadBackground();
    routeApi({ fieldMap: async () => signedMap("lever", 4) });

    const state = await detect(harness, "lever");

    expect(state.status === "tracked" && state.fieldMap?.version).toBe(4);
    expect(harness.store["fieldMapVersion:lever"]).toBe(4);
  });
});

// ---- D4-3: the anti-rollback ratchet is a read-check-write -----------------------

describe("the anti-rollback ratchet survives overlapping detections", () => {
  it("never lowers the floor when a lower version is accepted after a higher one raced it", async () => {
    const harness = await loadBackground({ storage: { "fieldMapVersion:lever": 3 }, storageLatencyMs: 8 });
    let call = 0;
    routeApi({
      fieldMap: async () => {
        call++;
        if (call === 1) return signedMap("lever", 5);
        await new Promise((resolve) => setTimeout(resolve, 1)); // the v3 response lands a hair later
        return signedMap("lever", 3);
      },
    });

    await Promise.all([detect(harness, "lever"), detect(harness, "lever")]);

    expect(harness.store["fieldMapVersion:lever"]).toBe(5);
  });

  it("still refuses a genuinely older version once a higher one was accepted", async () => {
    const harness = await loadBackground({ storage: { "fieldMapVersion:lever": 5 } });
    routeApi({ fieldMap: async () => signedMap("lever", 3) });

    const state = await detect(harness, "lever");

    expect(state.status === "tracked" && state.fieldMap).toBeNull();
    expect(state.status === "tracked" && state.fieldMapError).toMatch(/older version/);
    expect(harness.store["fieldMapVersion:lever"]).toBe(5);
  });
});

// ---- lookup URL and tracked state ---------------------------------------------------

describe("what the lookup sends", () => {
  it("asks about the posting URL only: no query string, no fragment, no /apply", async () => {
    const harness = await loadBackground();

    await detect(harness, "lever", "https://jobs.lever.co/acme/aaaa-1111/apply?lever-source=LinkedIn&token=SECRET#top");

    const lookup = apiPaths().find((path) => path.startsWith("/extension/lookup"))!;
    expect(lookup).toBe(`/extension/lookup?url=${encodeURIComponent("https://jobs.lever.co/acme/aaaa-1111")}`);
    expect(lookup).not.toMatch(/SECRET|lever-source/);
  });

  it("finds an Ashby application via its /application form route", async () => {
    const harness = await loadBackground();
    await detect(harness, "ashby");
    expect(apiPaths().find((path) => path.startsWith("/extension/lookup"))).toBe(
      `/extension/lookup?url=${encodeURIComponent("https://jobs.ashbyhq.com/acme/bbbb-2222")}`,
    );
  });

  it("refuses to look up a URL that isn't on the ATS's own host, without calling the API", async () => {
    const harness = await loadBackground();

    const state = await detect(harness, "lever", "https://evil.example/acme/aaaa-1111/apply");

    expect(state.status).toBe("error");
    expect(apiPaths()).toEqual([]);
  });
});

describe("tracked state carries the signed-in user", () => {
  it("records which user it was resolved for", async () => {
    const harness = await loadBackground();
    const state = await detect(harness, "greenhouse");
    expect(state.status === "tracked" && state.userId).toBe("user-1");
  });

  it("reports signed_out, without touching the API, when there is no session", async () => {
    mocks.getSession.mockResolvedValue({ data: { session: null } });
    const harness = await loadBackground();

    const state = await detect(harness, "greenhouse");

    expect(state).toEqual({ status: "signed_out" });
    expect(apiPaths()).toEqual([]);
  });

  it("VERIFY_SESSION answers yes only for the user who is signed in right now", async () => {
    const harness = await loadBackground();
    expect(await harness.send({ type: "VERIFY_SESSION", userId: "user-1" })).toEqual({ valid: true });
    expect(await harness.send({ type: "VERIFY_SESSION", userId: "user-2" })).toEqual({ valid: false });

    mocks.getSession.mockResolvedValue({ data: { session: null } });
    expect(await harness.send({ type: "VERIFY_SESSION", userId: "user-1" })).toEqual({ valid: false });
  });

  it("VERIFY_SESSION fails closed when the session can't be read", async () => {
    const harness = await loadBackground();
    mocks.getSession.mockRejectedValue(new Error("storage unavailable"));
    expect(await harness.send({ type: "VERIFY_SESSION", userId: "user-1" })).toEqual({ valid: false });
  });
});

// ---- E6 continuation: résumé/cover-letter PDFs move off PAGE_DETECTED --------------

describe("résumé/cover-letter PDFs are no longer fetched at detection time", () => {
  it("PAGE_DETECTED never calls apiFetchBlob, even when the payload says both files exist", async () => {
    const harness = await loadBackground();
    routeApi();
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/extension/lookup")) return { application_id: APP_ID };
      if (path === `/applications/${APP_ID}/extension-payload`) {
        return {
          prepare_result: { resume: { artifact_id: "a", version_id: "v" }, cover_letter: { artifact_id: "b", version_id: "w" } },
          personal_info: null,
        };
      }
      if (path.startsWith("/extension/field-maps/")) {
        const { ApiError } = await import("@/lib/api");
        throw new ApiError(404, "not found");
      }
      return {};
    });

    const state = await detect(harness, "greenhouse");

    expect(state.status).toBe("tracked");
    expect(state.status === "tracked" && state.resume).toBeNull();
    expect(state.status === "tracked" && state.coverLetter).toBeNull();
    expect(mocks.apiFetchBlob).not.toHaveBeenCalled();
  });
});

// ---- E6 continuation: FETCH_APPLICATION_FILES ---------------------------------------

function base64Of(text: string): string {
  return Buffer.from(text, "utf-8").toString("base64");
}

describe("FETCH_APPLICATION_FILES", () => {
  it("fetches only the files asked for, base64-encoded", async () => {
    const harness = await loadBackground();
    mocks.apiFetchBlob.mockImplementation(async (path: string) => {
      const text = path.endsWith("resume.pdf") ? "%PDF resume" : "%PDF cover letter";
      return new Blob([text], { type: "application/pdf" });
    });

    const result = await harness.send(
      { type: "FETCH_APPLICATION_FILES", applicationId: APP_ID, wantResume: true, wantCoverLetter: false },
      CONTENT_SCRIPT,
    );

    expect(result).toEqual({
      resume: { base64: base64Of("%PDF resume"), filename: "resume.pdf" },
      resumeError: null,
      coverLetter: null,
      coverLetterError: null,
    });
    expect(mocks.apiFetchBlob).toHaveBeenCalledTimes(1);
    // E6 continuation, part 2 -- moved to the extension's own mirrored route
    // (sign-out-aware), not the web app's shared /applications/... one.
    expect(mocks.apiFetchBlob).toHaveBeenCalledWith(`/extension/${APP_ID}/resume.pdf`);
  });

  it("fetches both when both are wanted", async () => {
    const harness = await loadBackground();
    mocks.apiFetchBlob.mockImplementation(async (path: string) => {
      const text = path.endsWith("resume.pdf") ? "%PDF resume" : "%PDF cover letter";
      return new Blob([text], { type: "application/pdf" });
    });

    const result = (await harness.send(
      { type: "FETCH_APPLICATION_FILES", applicationId: APP_ID, wantResume: true, wantCoverLetter: true },
      CONTENT_SCRIPT,
    )) as FetchApplicationFilesResult;

    expect(result.resume).toEqual({ base64: base64Of("%PDF resume"), filename: "resume.pdf" });
    expect(result.coverLetter).toEqual({ base64: base64Of("%PDF cover letter"), filename: "cover-letter.pdf" });
  });

  it("a résumé fetch failure doesn't block the cover letter -- each file fails independently", async () => {
    const harness = await loadBackground();
    mocks.apiFetchBlob.mockImplementation(async (path: string) => {
      if (path.endsWith("resume.pdf")) throw new Error("network hiccup");
      return new Blob(["%PDF cover letter"], { type: "application/pdf" });
    });

    const result = (await harness.send(
      { type: "FETCH_APPLICATION_FILES", applicationId: APP_ID, wantResume: true, wantCoverLetter: true },
      CONTENT_SCRIPT,
    )) as FetchApplicationFilesResult;

    expect(result.resume).toBeNull();
    expect(result.resumeError).toBe("network hiccup");
    expect(result.coverLetter).toEqual({ base64: base64Of("%PDF cover letter"), filename: "cover-letter.pdf" });
    expect(result.coverLetterError).toBeNull();
  });

  it("asking for neither calls apiFetchBlob zero times", async () => {
    const harness = await loadBackground();
    const result = await harness.send(
      { type: "FETCH_APPLICATION_FILES", applicationId: APP_ID, wantResume: false, wantCoverLetter: false },
      CONTENT_SCRIPT,
    );
    expect(result).toEqual({ resume: null, resumeError: null, coverLetter: null, coverLetterError: null });
    expect(mocks.apiFetchBlob).not.toHaveBeenCalled();
  });

  it("only a content script may send it -- the side panel is refused", async () => {
    const harness = await loadBackground();
    const reply = harness.send(
      { type: "FETCH_APPLICATION_FILES", applicationId: APP_ID, wantResume: true, wantCoverLetter: false },
      SIDE_PANEL,
    );
    expect(reply).toBeUndefined();
    expect(mocks.apiFetchBlob).not.toHaveBeenCalled();
  });

  it("rejects a malformed applicationId or non-boolean want flags", async () => {
    const harness = await loadBackground();
    await expect(
      harness.send(
        { type: "FETCH_APPLICATION_FILES", applicationId: "not-a-uuid", wantResume: true, wantCoverLetter: false },
        CONTENT_SCRIPT,
      ) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    await expect(
      harness.send(
        { type: "FETCH_APPLICATION_FILES", applicationId: APP_ID, wantResume: "yes", wantCoverLetter: false },
        CONTENT_SCRIPT,
      ) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    expect(mocks.apiFetchBlob).not.toHaveBeenCalled();
  });
});

// ---- E6 continuation / F2: SIGN_OUT ---------------------------------------------------
//
// Closes a real, confirmed gap: the backend's server-side extension
// sign-out revocation (POST /extension/sign-out) existed with nothing in
// the shipped extension ever calling it. These prove background.ts's own
// half of the wire-up -- App.test.tsx's own "signing out" tests prove the
// side panel actually sends this message as part of a real sign-out.

describe("SIGN_OUT", () => {
  it("POSTs to /extension/sign-out and reports ok", async () => {
    const harness = await loadBackground();

    const result = await harness.send({ type: "SIGN_OUT" }, SIDE_PANEL);

    expect(result).toEqual({ ok: true });
    expect(mocks.apiFetch).toHaveBeenCalledWith("/extension/sign-out", { method: "POST" });
  });

  it("never throws -- a failed revocation call reports ok: false instead", async () => {
    mocks.apiFetch.mockRejectedValueOnce(new Error("network down"));
    const harness = await loadBackground();

    const result = await harness.send({ type: "SIGN_OUT" }, SIDE_PANEL);

    expect(result).toEqual({ ok: false });
  });

  it("only the side panel may send it -- a content script is refused", async () => {
    const harness = await loadBackground();

    const reply = harness.send({ type: "SIGN_OUT" }, CONTENT_SCRIPT);

    expect(reply).toBeUndefined();
    expect(mocks.apiFetch).not.toHaveBeenCalledWith("/extension/sign-out", expect.anything());
  });
});

// ---- BG-1 / F12 / AB-13: who may send what --------------------------------------------

describe("messages are accepted only from the sender kind that legitimately sends them", () => {
  it("ignores MARK_APPLIED from a content script -- the least-trusted extension context", async () => {
    const harness = await loadBackground();

    const reply = harness.send({ type: "MARK_APPLIED", applicationId: APP_ID, idempotencyKey: "k" }, CONTENT_SCRIPT);

    expect(reply).toBeUndefined();
    expect(apiPaths()).toEqual([]);
  });

  it.each([
    ["MATCH_ANSWER", { normalizedQuestion: "why us?" }],
    ["SAVE_ANSWER", { normalizedQuestion: "why us?", answerText: "because" }],
    ["DRAFT_ANSWER", { applicationId: APP_ID, questionText: "why us?" }],
  ])("ignores %s from a content script", async (type, fields) => {
    const harness = await loadBackground();
    expect(harness.send({ type, ...fields }, CONTENT_SCRIPT)).toBeUndefined();
    expect(apiPaths()).toEqual([]);
  });

  it("accepts MARK_APPLIED from the side panel", async () => {
    const harness = await loadBackground();

    const reply = await harness.send({ type: "MARK_APPLIED", applicationId: APP_ID, idempotencyKey: "k" }, SIDE_PANEL);

    expect(reply).toEqual({ ok: true, status: "applied" });
    expect(apiPaths()).toEqual([`/applications/${APP_ID}/stage`]);
  });

  it("recognizes the side panel by its origin too, in case the browser reports no url", async () => {
    const harness = await loadBackground();
    const originOnly = { id: EXTENSION_ID, origin: `chrome-extension://${EXTENSION_ID}` };
    expect(await harness.send({ type: "MARK_APPLIED", applicationId: APP_ID, idempotencyKey: "k" }, originOnly)).toEqual({
      ok: true,
      status: "applied",
    });
  });

  it("a content script can't pass as the extension by claiming a page URL that merely mentions it", async () => {
    const harness = await loadBackground();
    const spoof = {
      id: EXTENSION_ID,
      url: `https://jobs.lever.co/?chrome-extension://${EXTENSION_ID}/`,
      origin: "https://jobs.lever.co",
      tab: { id: 7 },
    };
    expect(harness.send({ type: "MARK_APPLIED", applicationId: APP_ID, idempotencyKey: "k" }, spoof)).toBeUndefined();
    expect(apiPaths()).toEqual([]);
  });

  it("ignores PAGE_DETECTED and VERIFY_SESSION from anything but a content script", async () => {
    const harness = await loadBackground();
    expect(harness.send({ type: "PAGE_DETECTED", atsType: "lever", url: CONTENT_SCRIPT.url }, SIDE_PANEL)).toBeUndefined();
    expect(harness.send({ type: "VERIFY_SESSION", userId: "user-1" }, SIDE_PANEL)).toBeUndefined();
    expect(apiPaths()).toEqual([]);
  });

  it("ignores everything from another extension or an unidentified sender", async () => {
    const harness = await loadBackground();
    const foreign = { id: "some-other-extension", url: "chrome-extension://some-other-extension/x.html" };
    expect(harness.send({ type: "MARK_APPLIED", applicationId: APP_ID, idempotencyKey: "k" }, foreign)).toBeUndefined();
    expect(harness.send({ type: "MARK_APPLIED", applicationId: APP_ID, idempotencyKey: "k" }, {})).toBeUndefined();
    expect(apiPaths()).toEqual([]);
  });

  it("ignores messages it doesn't know, including PAGE_CHANGED (a broadcast meant for the panel)", async () => {
    const harness = await loadBackground();
    expect(harness.send({ type: "PAGE_CHANGED" }, CONTENT_SCRIPT)).toBeUndefined();
    expect(harness.send(null, CONTENT_SCRIPT)).toBeUndefined();
    expect(harness.send("MARK_APPLIED", SIDE_PANEL)).toBeUndefined();
  });
});

describe("message fields are validated before they reach a request path or body", () => {
  it("rejects an atsType that isn't one of the three, without calling the API", async () => {
    const harness = await loadBackground();
    await expect(
      harness.send({ type: "PAGE_DETECTED", atsType: "../../admin", url: CONTENT_SCRIPT.url }) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    expect(apiPaths()).toEqual([]);
  });

  it.each(["../../x", "not-a-uuid", "", "0b7f3c1e-5d2a-4e8b-9c41-2f6a8d9e0b13/../../x"])(
    "rejects a MARK_APPLIED applicationId of %j",
    async (applicationId) => {
      const harness = await loadBackground();
      await expect(
        harness.send({ type: "MARK_APPLIED", applicationId, idempotencyKey: "k" }, SIDE_PANEL) as Promise<unknown>,
      ).rejects.toThrow(/invalid request/i);
      expect(apiPaths()).toEqual([]);
    },
  );

  it("rejects an oversized question or answer", async () => {
    const harness = await loadBackground();
    await expect(
      harness.send({ type: "DRAFT_ANSWER", applicationId: APP_ID, questionText: "q".repeat(501) }, SIDE_PANEL) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    await expect(
      harness.send({ type: "SAVE_ANSWER", normalizedQuestion: "q", answerText: "a".repeat(5_001) }, SIDE_PANEL) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    await expect(
      harness.send({ type: "MATCH_ANSWER", normalizedQuestion: "q".repeat(501) }, SIDE_PANEL) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    expect(apiPaths()).toEqual([]);
  });

  it("rejects non-string fields", async () => {
    const harness = await loadBackground();
    await expect(
      harness.send({ type: "SAVE_ANSWER", normalizedQuestion: 7, answerText: {} }, SIDE_PANEL) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    expect(apiPaths()).toEqual([]);
  });

  it("still passes a well-formed DRAFT_ANSWER and MATCH_ANSWER through", async () => {
    const harness = await loadBackground();
    expect(await harness.send({ type: "DRAFT_ANSWER", applicationId: APP_ID, questionText: "Why us?" }, SIDE_PANEL)).toMatchObject({
      eligible: true,
    });
    expect(await harness.send({ type: "MATCH_ANSWER", normalizedQuestion: "why us?" }, SIDE_PANEL)).toEqual({ answer: null });
  });
});
