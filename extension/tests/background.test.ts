import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { SignedFieldMapResponse } from "@/lib/ats-field-map";
import { CONSENT_REQUIRED_MESSAGE, CONSENT_STORAGE_KEY, CONSENT_VERSION } from "@/lib/consent";
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
const ANSWER_ID = "7d3f5c2a-9b1e-4f60-8a27-3c5d1e9b4a10";

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

/** Pass as `consent` to start with no stored consent flag. */
const NO_CONSENT = Symbol("no stored consent");

async function loadBackground(
  options: { storage?: Record<string, unknown>; storageLatencyMs?: number; consent?: unknown } = {},
) {
  vi.resetModules();
  const store: Record<string, unknown> = { ...(options.storage ?? {}) };
  // The consent flag lives in the same chrome.storage.local, but is held apart
  // here so `store` keeps showing only what the field-map ratchet wrote.
  const consent: { value: unknown } = {
    value: options.consent === undefined ? { version: CONSENT_VERSION } : options.consent,
  };
  const delay = () => new Promise((resolve) => setTimeout(resolve, options.storageLatencyMs ?? 0));
  vi.stubGlobal("chrome", {
    storage: {
      local: {
        get: async (key: string) => {
          await delay();
          if (key === CONSENT_STORAGE_KEY) {
            return consent.value === NO_CONSENT || consent.value === undefined ? {} : { [key]: consent.value };
          }
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
    setConsent: (value: unknown) => {
      consent.value = value;
    },
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

  it("sends the posting address for an application form whether or not the job is tracked, and requests nothing else", async () => {
    // The Privacy Policy says so ("whether or not you do"). The lookup is how tracking is found
    // out, so it cannot wait for it: an untracked page's address reaches the API too.
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/extension/lookup")) return { application_id: null };
      return {};
    });
    const harness = await loadBackground();

    const state = await detect(harness, "greenhouse");

    expect(state).toEqual({ status: "untracked" });
    expect(apiPaths()).toEqual([
      `/extension/lookup?url=${encodeURIComponent("https://job-boards.greenhouse.io/acme/jobs/1")}`,
    ]);
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

// ---- A: the consent gate ---------------------------------------------------------------
//
// The service worker is the second lock behind the content script's own: nothing that
// sends an address, a question or an application id to the API, or downloads the
// person's documents, runs unless the stored consent flag is valid for this build's
// disclosure. The flag is read afresh on each request.

describe("the consent gate", () => {
  const withoutConsent = { consent: NO_CONSENT };

  it("without consent, a page detection sends no lookup request and says why", async () => {
    const harness = await loadBackground(withoutConsent);

    const state = await detect(harness, "greenhouse");

    expect(state).toEqual({ status: "consent_required" });
    expect(apiPaths()).toEqual([]);
    expect(mocks.getSession).not.toHaveBeenCalled();
  });

  it.each([
    ["MATCH_ANSWER", { normalizedQuestion: "why us?" }, SIDE_PANEL],
    ["SAVE_ANSWER", { normalizedQuestion: "why us?", answerText: "because" }, SIDE_PANEL],
    ["DRAFT_ANSWER", { applicationId: APP_ID, questionText: "Why us?" }, SIDE_PANEL],
    ["MARK_APPLIED", { applicationId: APP_ID, idempotencyKey: "k" }, SIDE_PANEL],
    ["ANSWER_USED", { answerId: ANSWER_ID }, SIDE_PANEL],
    ["FETCH_APPLICATION_FILES", { applicationId: APP_ID, wantResume: true, wantCoverLetter: true }, CONTENT_SCRIPT],
  ])("without consent, %s is refused and nothing reaches the API", async (type, fields, sender) => {
    const harness = await loadBackground(withoutConsent);

    await expect(harness.send({ type, ...fields }, sender) as Promise<unknown>).rejects.toThrow(CONSENT_REQUIRED_MESSAGE);

    expect(apiPaths()).toEqual([]);
    expect(mocks.apiFetchBlob).not.toHaveBeenCalled();
  });

  it("sign-out still works without consent: ending a session must never be blocked", async () => {
    const harness = await loadBackground(withoutConsent);

    expect(await harness.send({ type: "SIGN_OUT" }, SIDE_PANEL)).toEqual({ ok: true });
    expect(apiPaths()).toEqual(["/extension/sign-out"]);
  });

  it("with consent everything runs as before", async () => {
    const harness = await loadBackground();

    expect((await detect(harness, "greenhouse")).status).toBe("tracked");
    expect(await harness.send({ type: "MATCH_ANSWER", normalizedQuestion: "why us?" }, SIDE_PANEL)).toEqual({ answer: null });
  });

  it("consent withdrawn mid-session stops the very next request", async () => {
    const harness = await loadBackground();
    expect((await detect(harness, "greenhouse")).status).toBe("tracked");
    const callsBefore = apiPaths().length;

    harness.setConsent(undefined);

    expect(await detect(harness, "greenhouse")).toEqual({ status: "consent_required" });
    await expect(
      harness.send({ type: "DRAFT_ANSWER", applicationId: APP_ID, questionText: "Why us?" }, SIDE_PANEL) as Promise<unknown>,
    ).rejects.toThrow(CONSENT_REQUIRED_MESSAGE);
    expect(apiPaths()).toHaveLength(callsBefore);
  });

  it("consent withdrawn while the lookup was running: the application's id goes into no further request", async () => {
    const harness = await loadBackground();
    mocks.apiFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/extension/lookup")) {
        harness.setConsent(undefined); // the person withdraws while the lookup is in flight
        return { application_id: APP_ID };
      }
      return {};
    });

    const state = await detect(harness, "greenhouse");

    expect(state).toEqual({ status: "consent_required" });
    expect(apiPaths().filter((p) => p.startsWith("/extension/lookup"))).toHaveLength(1);
    expect(apiPaths()).not.toContain(`/applications/${APP_ID}/extension-payload`);
    expect(apiPaths().some((p) => p.startsWith("/extension/field-maps/"))).toBe(false);
  });

  it("a consent version bump closes the gate for a flag stored under the old version", async () => {
    const harness = await loadBackground({ consent: { version: CONSENT_VERSION } });
    expect((await detect(harness, "greenhouse")).status).toBe("tracked");
    const callsBefore = apiPaths().length;

    harness.setConsent({ version: CONSENT_VERSION + 1 });

    expect(await detect(harness, "greenhouse")).toEqual({ status: "consent_required" });
    expect(apiPaths()).toHaveLength(callsBefore);

    harness.setConsent({ version: CONSENT_VERSION - 1 });
    expect(await detect(harness, "greenhouse")).toEqual({ status: "consent_required" });
    expect(apiPaths()).toHaveLength(callsBefore);
  });

  it("agreeing again reopens it without restarting the worker", async () => {
    const harness = await loadBackground(withoutConsent);
    expect(await detect(harness, "greenhouse")).toEqual({ status: "consent_required" });

    harness.setConsent({ version: CONSENT_VERSION });

    expect((await detect(harness, "greenhouse")).status).toBe("tracked");
  });

  it("an unreadable flag is no consent", async () => {
    const harness = await loadBackground();
    vi.stubGlobal("chrome", {
      storage: {
        local: {
          get: async () => {
            throw new Error("storage unavailable");
          },
        },
      },
    });

    expect(await detect(harness, "greenhouse")).toEqual({ status: "consent_required" });
    expect(apiPaths()).toEqual([]);
  });
});


// ---- B: the intent and jurisdiction a remembered answer travels with --------------------

describe("MATCH_ANSWER and SAVE_ANSWER carry the intent and the jurisdiction", () => {
  const bodyOf = (path: string) => {
    const call = mocks.apiFetch.mock.calls.find((c) => c[0] === path);
    return JSON.parse((call?.[1] as { body: string }).body) as Record<string, unknown>;
  };

  it("a lookup sends canonical_intent and jurisdiction in the request body", async () => {
    const harness = await loadBackground();

    await harness.send(
      { type: "MATCH_ANSWER", normalizedQuestion: "why do you want to work here?", canonicalIntent: "why_this_company", jurisdiction: "US" },
      SIDE_PANEL,
    );

    expect(bodyOf("/extension/match-answer")).toEqual({
      normalized_question: "why do you want to work here?",
      canonical_intent: "why_this_company",
      jurisdiction: "US",
    });
  });

  it("a save sends them too", async () => {
    const harness = await loadBackground();

    await harness.send(
      {
        type: "SAVE_ANSWER",
        normalizedQuestion: "are you legally authorized to work in the united states?",
        answerText: "Yes",
        canonicalIntent: "work_authorization",
        jurisdiction: "US",
      },
      SIDE_PANEL,
    );

    expect(bodyOf("/extension/approved-answers")).toEqual({
      normalized_question: "are you legally authorized to work in the united states?",
      answer_text: "Yes",
      canonical_intent: "work_authorization",
      jurisdiction: "US",
    });
  });

  it("a tag that is absent is absent from the body -- never an empty string or null", async () => {
    const harness = await loadBackground();

    await harness.send({ type: "MATCH_ANSWER", normalizedQuestion: "q" }, SIDE_PANEL);
    await harness.send({ type: "SAVE_ANSWER", normalizedQuestion: "q", answerText: "a" }, SIDE_PANEL);

    expect(bodyOf("/extension/match-answer")).toEqual({ normalized_question: "q" });
    expect(bodyOf("/extension/approved-answers")).toEqual({ normalized_question: "q", answer_text: "a" });
  });

  it.each([
    ["an intent with upper case or spaces", { canonicalIntent: "Why Us" }],
    ["an intent that is not a name at all", { canonicalIntent: "../../x" }],
    ["an over-long intent", { canonicalIntent: "a".repeat(65) }],
    ["a jurisdiction that is not an ISO code", { jurisdiction: "usa" }],
    ["a lower-case jurisdiction", { jurisdiction: "us" }],
    ["a jurisdiction that is not a string", { jurisdiction: 7 }],
    ["an empty intent", { canonicalIntent: "" }],
  ])("rejects %s without calling the API", async (_name, tags) => {
    const harness = await loadBackground();

    await expect(
      harness.send({ type: "MATCH_ANSWER", normalizedQuestion: "q", ...tags }, SIDE_PANEL) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    await expect(
      harness.send({ type: "SAVE_ANSWER", normalizedQuestion: "q", answerText: "a", ...tags }, SIDE_PANEL) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
    expect(apiPaths()).toEqual([]);
  });
});

// ---- C: a remembered answer was used --------------------------------------------------------

describe("ANSWER_USED", () => {
  it("POSTs to the answer's own used route and reports ok", async () => {
    const harness = await loadBackground();

    const result = await harness.send({ type: "ANSWER_USED", answerId: ANSWER_ID }, SIDE_PANEL);

    expect(result).toEqual({ ok: true });
    expect(mocks.apiFetch).toHaveBeenCalledWith(`/extension/answers/${ANSWER_ID}/used`, { method: "POST" });
  });

  it("never throws: a failed count (the answer is gone, the network is down) is ok:false", async () => {
    mocks.apiFetch.mockRejectedValueOnce(new Error("not found"));
    const harness = await loadBackground();

    expect(await harness.send({ type: "ANSWER_USED", answerId: ANSWER_ID }, SIDE_PANEL)).toEqual({ ok: false });
  });

  it("only the side panel may send it", async () => {
    const harness = await loadBackground();

    expect(harness.send({ type: "ANSWER_USED", answerId: ANSWER_ID }, CONTENT_SCRIPT)).toBeUndefined();
    expect(apiPaths()).toEqual([]);
  });

  it.each(["../../x", "not-a-uuid", "", `${ANSWER_ID}/../../x`, 7, null])("rejects an answer id of %j before any path is built", async (answerId) => {
    const harness = await loadBackground();

    await expect(harness.send({ type: "ANSWER_USED", answerId }, SIDE_PANEL) as Promise<unknown>).rejects.toThrow(
      /invalid request/i,
    );
    expect(apiPaths()).toEqual([]);
  });
});


// ---- G: the fill-outcome report ----------------------------------------------------------------

describe("REPORT_FILL_OUTCOME", () => {
  const GREENHOUSE_PAGE = { id: EXTENSION_ID, url: "https://job-boards.greenhouse.io/acme/jobs/1", tab: { id: 7 } };
  const report = (overrides: Record<string, unknown> = {}) => ({
    type: "REPORT_FILL_OUTCOME",
    atsType: "greenhouse",
    applicationId: APP_ID,
    fieldsAttempted: 4,
    fieldsFilled: 4,
    outcome: "ok",
    ...overrides,
  });
  const postedBody = () => {
    const call = mocks.apiFetch.mock.calls.find((c) => c[0] === "/extension/fill-outcome");
    return call === undefined ? undefined : JSON.parse((call[1] as { body: string }).body);
  };

  it.each([
    ["success", { fieldsAttempted: 4, fieldsFilled: 4, outcome: "ok" }],
    ["a partial fill", { fieldsAttempted: 5, fieldsFilled: 3, outcome: "partial" }],
    ["a failed fill", { fieldsAttempted: 2, fieldsFilled: 0, outcome: "failed" }],
  ])("%s: posts exactly the five fields the service accepts", async (_name, counts) => {
    const harness = await loadBackground();

    const result = await harness.send(report(counts), GREENHOUSE_PAGE);

    expect(result).toEqual({ ok: true });
    expect(mocks.apiFetch).toHaveBeenCalledWith("/extension/fill-outcome", expect.objectContaining({ method: "POST" }));
    expect(postedBody()).toEqual({
      ats_type: "greenhouse",
      application_id: APP_ID,
      fields_attempted: counts.fieldsAttempted,
      fields_filled: counts.fieldsFilled,
      outcome: counts.outcome,
    });
  });

  it("the service's ceiling is inclusive: a report of exactly 1000 is accepted, 1001 is not", async () => {
    const harness = await loadBackground();

    expect(await harness.send(report({ fieldsAttempted: 1000, fieldsFilled: 1000 }), GREENHOUSE_PAGE)).toEqual({ ok: true });
    expect(postedBody()).toMatchObject({ fields_attempted: 1000, fields_filled: 1000 });
    await expect(
      harness.send(report({ fieldsAttempted: 1001, fieldsFilled: 1001 }), GREENHOUSE_PAGE) as Promise<unknown>,
    ).rejects.toThrow(/invalid request/i);
  });

  it("anything else the message carries is dropped, never forwarded", async () => {
    const harness = await loadBackground();

    await harness.send(
      report({ label: "Why us?", value: "alice@example.com", url: "https://job-boards.greenhouse.io/acme/jobs/1?token=1", pageText: "..." }),
      GREENHOUSE_PAGE,
    );

    expect(Object.keys(postedBody()).sort()).toEqual(["application_id", "ats_type", "fields_attempted", "fields_filled", "outcome"]);
    expect(JSON.stringify(postedBody())).not.toMatch(/Why us|alice|token|pageText/);
  });

  it("nothing is sent without consent, and the refusal is silent to the page", async () => {
    const harness = await loadBackground({ consent: NO_CONSENT });

    await expect(harness.send(report(), GREENHOUSE_PAGE) as Promise<unknown>).rejects.toThrow(CONSENT_REQUIRED_MESSAGE);

    expect(apiPaths()).toEqual([]);
  });

  it("consent withdrawn mid-session stops the next report", async () => {
    const harness = await loadBackground();
    expect(await harness.send(report(), GREENHOUSE_PAGE)).toEqual({ ok: true });

    harness.setConsent(undefined);

    await expect(harness.send(report(), GREENHOUSE_PAGE) as Promise<unknown>).rejects.toThrow(CONSENT_REQUIRED_MESSAGE);
    expect(apiPaths().filter((p) => p === "/extension/fill-outcome")).toHaveLength(1);
  });

  it("a failed delivery is { ok: false } -- it never throws", async () => {
    mocks.apiFetch.mockRejectedValueOnce(new Error("network down"));
    const harness = await loadBackground();

    expect(await harness.send(report(), GREENHOUSE_PAGE)).toEqual({ ok: false });
  });

  it("only a content script may report", async () => {
    const harness = await loadBackground();

    expect(harness.send(report(), SIDE_PANEL)).toBeUndefined();
    expect(apiPaths()).toEqual([]);
  });

  it.each([
    ["an unknown ATS", { atsType: "workday" }],
    ["an ATS that is not the sender page's own", { atsType: "lever" }],
    ["a malformed application id", { applicationId: "../../x" }],
    ["a missing application id", { applicationId: undefined }],
    ["more filled than attempted", { fieldsAttempted: 2, fieldsFilled: 3 }],
    ["a negative count", { fieldsAttempted: -1, fieldsFilled: -1 }],
    ["a fractional count", { fieldsAttempted: 2.5, fieldsFilled: 2 }],
    ["a count as text", { fieldsAttempted: "4" }],
    ["a count over the service's ceiling", { fieldsAttempted: 1001, fieldsFilled: 1001 }],
    ["an outcome the service does not know", { outcome: "setup_required" }],
    ["an outcome that is not a string", { outcome: 1 }],
  ])("rejects %s without calling the API", async (_name, overrides) => {
    const harness = await loadBackground();

    await expect(harness.send(report(overrides), GREENHOUSE_PAGE) as Promise<unknown>).rejects.toThrow(/invalid request/i);

    expect(apiPaths()).toEqual([]);
  });

  it("a sender with no page URL is judged on the message alone", async () => {
    const harness = await loadBackground();

    expect(await harness.send(report(), { id: EXTENSION_ID, tab: { id: 7 } })).toEqual({ ok: true });
  });
});
