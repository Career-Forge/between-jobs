import { afterEach, describe, expect, it, vi } from "vitest";
import type { AtsFieldMap } from "@/lib/ats-field-map";
import type { DetectionStateResponse, FillFieldResult, FillResult, TabState } from "@/lib/types";
import { LEVER_MAP, loadContentScript, PERSON, settle, trackedState } from "./helpers/contentHarness";

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

// ---- DOM builders ----------------------------------------------------------

function buildLeverForm(extra = ""): void {
  document.body.innerHTML = `
    <form>
      <input type="file" name="resume" />
      <input type="text" name="name" />
      <input type="email" name="email" />
      <input type="text" name="phone" />
      <input type="text" name="location" />
      ${extra}
    </form>`;
}

function buildGreenhouseForm(extra = ""): void {
  document.body.innerHTML = `
    <form>
      <input type="text" id="first_name" /><input type="text" id="last_name" />
      <input type="text" id="email" /><input type="tel" id="phone" />
      <input type="file" id="resume" />
      ${extra}
    </form>`;
}

function buildAshbyForm(extra = ""): void {
  document.body.innerHTML = `
    <div>
      <div data-field-path="_systemfield_name"><input type="text" id="_systemfield_name" name="_systemfield_name" /></div>
      <div data-field-path="_systemfield_email"><input type="email" id="_systemfield_email" name="_systemfield_email" /></div>
      <input type="file" id="_systemfield_resume" />
      ${extra}
    </div>`;
}

const value = (selector: string) => document.querySelector<HTMLInputElement>(selector)!.value;

const LEVER_URL = "https://jobs.lever.co/acme/aaaa-1111/apply";
const GREENHOUSE_URL = "https://job-boards.greenhouse.io/acme/jobs/1";
const ASHBY_URL = "https://jobs.ashbyhq.com/acme/bbbb-2222/application";

/** Stands in for the service worker: PAGE_DETECTED gets `state`, VERIFY_SESSION says yes. */
function backend(state: TabState | ((url: string) => TabState), session = true) {
  return (message: { type: string; [key: string]: unknown }): unknown => {
    if (message.type === "PAGE_DETECTED") {
      return typeof state === "function" ? state(String(message.url)) : state;
    }
    if (message.type === "VERIFY_SESSION") return { valid: session };
    return undefined;
  };
}

const fillOf = (reply: unknown) => reply as FillResult | null;

// ---- WS-1: a bad selector in a verified map must not abort the fill ----------

describe("a malformed selector in a verified map's standard_fields costs one field, not the fill", () => {
  it("Lever: fills the generic fields and the valid map field, and reports the bad selector", async () => {
    buildLeverForm();
    const fieldMap: AtsFieldMap = {
      ...LEVER_MAP,
      standard_fields: [
        { field: "broken", selector: "input[", strategy: "direct", profileFields: ["linkedin"] },
        ...LEVER_MAP.standard_fields,
      ],
    };
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState({ fieldMap })) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply).not.toBeNull();
    expect(value('input[name="name"]')).toBe("Alice Example");
    expect(value('input[name="email"]')).toBe("alice@example.com");
    expect(value('input[name="location"]')).toBe("New York, NY, US");
    expect(reply!.fieldMapError).toMatch(/invalid selector \(input\[\)/);
    expect(reply!.fillError).toBeNull();
  });

  it("Greenhouse: same", async () => {
    buildGreenhouseForm();
    const fieldMap: AtsFieldMap = {
      ats_type: "greenhouse",
      version: 1,
      schema: "ats-field-map/v1",
      standard_fields: [{ field: "broken", selector: "###", strategy: "direct", profileFields: ["linkedin"] }],
    };
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState({ fieldMap })) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply).not.toBeNull();
    expect(value("#first_name")).toBe("Alice");
    expect(value("#email")).toBe("alice@example.com");
    expect(reply!.fieldMapError).toMatch(/invalid selector \(###\)/);
  });

  it("Ashby: same", async () => {
    buildAshbyForm();
    const fieldMap: AtsFieldMap = {
      ats_type: "ashby",
      version: 1,
      schema: "ats-field-map/v1",
      standard_fields: [{ field: "broken", selector: "input[", strategy: "direct", profileFields: ["linkedin"] }],
    };
    const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState({ fieldMap })) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply).not.toBeNull();
    expect(value("#_systemfield_name")).toBe("Alice Example");
    expect(reply!.fieldMapError).toMatch(/invalid selector/);
  });

  it("an unexpected throw during a fill comes back as a FillResult with fillError -- never as no reply at all", async () => {
    buildLeverForm();
    // A map whose standard_fields isn't an array: spreading it throws a TypeError
    // inside the fill, standing in for any bug the fill code might have.
    const corrupt = { ...LEVER_MAP, standard_fields: undefined } as unknown as AtsFieldMap;
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState({ fieldMap: corrupt })) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply).not.toBeNull();
    expect(reply!.fillError).toEqual(expect.any(String));
    expect(reply!.fillError).not.toBe("");
  });
});

// ---- WS-3 / AB-04: state is bound to the URL it was resolved for -------------

describe("state is bound to the page it was resolved for", () => {
  const respondByUrl = (url: string): TabState =>
    url.endsWith("/jobs/1") ? trackedState({ applicationId: "app-A" }) : trackedState({ applicationId: "app-B" });

  it("REQUEST_FILL after a client-side navigation refuses to fill posting B with application A's data", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(respondByUrl) });
    const before = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;
    expect(before.tabState?.status === "tracked" && before.tabState.applicationId).toBe("app-A");

    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");
    const reply = await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });

    expect(reply).toBeNull();
    expect(value("#first_name")).toBe("");
    expect(value("#email")).toBe("");
  });

  it("...and re-detects, so the next request acts on application B", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(respondByUrl) });
    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.settle();

    const detected = harness.sentOfType("PAGE_DETECTED");
    expect(detected.map((m) => m.url)).toEqual([GREENHOUSE_URL, "https://job-boards.greenhouse.io/acme/jobs/2"]);
    const now = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;
    expect(now.tabState?.status === "tracked" && now.tabState.applicationId).toBe("app-B");
    expect(fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }))).not.toBeNull();
  });

  it("GET_DETECTION_STATE never serves application A's state for page B", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(respondByUrl) });
    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");

    const state = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;

    expect(state.tabState?.status === "tracked" && state.tabState.applicationId).toBe("app-B");
  });

  it("FILL_FIELD after a client-side navigation writes nothing and says why", async () => {
    buildGreenhouseForm(`<label for="question_1">Why us?</label><textarea id="question_1"></textarea>`);
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(respondByUrl) });
    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");

    const reply = (await harness.send({ type: "FILL_FIELD", fieldName: "question_1", value: "draft" })) as FillFieldResult;

    expect(reply).toEqual({ filled: false, reason: "page_changed" });
    expect(value("#question_1")).toBe("");
  });

  it("a wxt:locationchange drops the old state, tells the panel, and looks the NEW page up -- WXT raises it before location.href has updated", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(respondByUrl) });

    // The Navigation API's `navigate` event (what WXT listens to) fires while
    // location.href still holds the page being left; the URL moves right after.
    harness.fireLocationChange();
    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");
    await harness.settle();

    expect(harness.sentOfType("PAGE_CHANGED").length).toBeGreaterThanOrEqual(1);
    expect(harness.sentOfType("PAGE_DETECTED").map((m) => m.url)).toEqual([
      GREENHOUSE_URL,
      "https://job-boards.greenhouse.io/acme/jobs/2",
    ]);
  });

  it("a slow lookup for the OLD page can't overwrite the new page's state", async () => {
    buildGreenhouseForm();
    let releaseFirst: (state: TabState) => void = () => {};
    const first = new Promise<TabState>((resolve) => {
      releaseFirst = resolve;
    });
    let calls = 0;
    const respond = (message: { type: string; [key: string]: unknown }): unknown => {
      if (message.type === "VERIFY_SESSION") return { valid: true };
      if (message.type !== "PAGE_DETECTED") return undefined;
      calls++;
      return calls === 1 ? first : trackedState({ applicationId: "app-B" });
    };
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond });

    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");
    harness.fireLocationChange();
    await harness.settle();
    releaseFirst(trackedState({ applicationId: "app-A" }));
    await harness.settle();

    const state = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;
    expect(state.tabState?.status === "tracked" && state.tabState.applicationId).toBe("app-B");
  });
});

// ---- F6: formDetected is evaluated when asked, not once at load --------------

describe("a form that appears after the content script started is detected", () => {
  it("RECHECK finds an Ashby form that rendered after load (it used to return the load-time 'no form' forever)", async () => {
    document.body.innerHTML = `<div><a href="/application">Apply for this Job</a></div>`;
    const harness = await loadContentScript({ href: "https://jobs.ashbyhq.com/acme/bbbb-2222", respond: backend(trackedState()) });
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(0);

    buildAshbyForm();
    harness.navigate(ASHBY_URL);
    const reply = (await harness.send({ type: "RECHECK" })) as DetectionStateResponse;

    expect(reply.formDetected).toBe(true);
    expect(reply.tabState?.status).toBe("tracked");
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(1);
  });

  it("GET_DETECTION_STATE alone picks a late-rendered form up too, once", async () => {
    document.body.innerHTML = `<div>listing</div>`;
    const harness = await loadContentScript({ href: "https://jobs.ashbyhq.com/acme/bbbb-2222", respond: backend(trackedState()) });

    buildAshbyForm();
    harness.navigate(ASHBY_URL);
    const first = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;
    await harness.send({ type: "GET_DETECTION_STATE" });

    expect(first.formDetected).toBe(true);
    expect(first.tabState?.status).toBe("tracked");
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(1);
  });

  it("reports no form once the page no longer has one", async () => {
    buildAshbyForm();
    const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState()) });
    document.body.innerHTML = "<div>somewhere else</div>";

    const reply = (await harness.send({ type: "RECHECK" })) as DetectionStateResponse;

    expect(reply.formDetected).toBe(false);
  });
});

// ---- AB-03: a different signed-in user must not inherit the cached state ------

describe("cached state doesn't outlive the user it was resolved for", () => {
  it("won't fill after the session changed to another user", async () => {
    buildLeverForm();
    let sessionValid = true;
    const respond = (message: { type: string; [key: string]: unknown }): unknown => {
      if (message.type === "PAGE_DETECTED") return trackedState({ userId: "user-1" });
      if (message.type === "VERIFY_SESSION") return { valid: sessionValid && message.userId === "user-1" };
      return undefined;
    };
    const harness = await loadContentScript({ href: LEVER_URL, respond });
    expect(fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }))).not.toBeNull();
    document.querySelectorAll<HTMLInputElement>("input[type=text],input[type=email]").forEach((i) => (i.value = ""));

    sessionValid = false; // signed out, then someone else signed in
    const reply = await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });

    expect(reply).toBeNull();
    expect(value('input[name="name"]')).toBe("");
    expect(value('input[name="email"]')).toBe("");
  });

  it("GET_DETECTION_STATE drops it and re-detects instead of returning it", async () => {
    buildLeverForm();
    let currentUser = "user-1";
    const respond = (message: { type: string; [key: string]: unknown }): unknown => {
      if (message.type === "PAGE_DETECTED") return trackedState({ userId: currentUser, applicationId: `app-for-${currentUser}` });
      if (message.type === "VERIFY_SESSION") return { valid: message.userId === currentUser };
      return undefined;
    };
    const harness = await loadContentScript({ href: LEVER_URL, respond });

    currentUser = "user-2";
    const reply = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;

    expect(reply.tabState?.status === "tracked" && reply.tabState.applicationId).toBe("app-for-user-2");
  });

  it("asks the service worker about the state's own user id, and nothing more", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState({ userId: "user-1" })) });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });

    expect(harness.sentOfType("VERIFY_SESSION")).toEqual([{ type: "VERIFY_SESSION", userId: "user-1" }]);
  });
});

// ---- FILL_FIELD gating and outcomes -------------------------------------------

describe("FILL_FIELD", () => {
  const QUESTION = `<label for="question_1">Why us?</label><textarea id="question_1"></textarea>`;

  it("writes on Greenhouse only for a tracked page (it used to write for an untracked one too)", async () => {
    buildGreenhouseForm(QUESTION);
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend({ status: "untracked" }) });

    const reply = (await harness.send({ type: "FILL_FIELD", fieldName: "question_1", value: "draft" })) as FillFieldResult;

    expect(reply).toEqual({ filled: false, reason: "refused" });
    expect(value("#question_1")).toBe("");
  });

  it("writes on Ashby only for a tracked page", async () => {
    buildAshbyForm(`
      <div data-field-path="q-1"><label class="ashby-application-form-question-title" for="q-1">Why us?</label>
      <textarea id="q-1" name="q-1"></textarea></div>`);
    const harness = await loadContentScript({ href: ASHBY_URL, respond: backend({ status: "signed_out" }) });

    const reply = (await harness.send({ type: "FILL_FIELD", fieldName: "q-1", value: "draft" })) as FillFieldResult;

    expect(reply.filled).toBe(false);
    expect(value("#q-1")).toBe("");
  });

  it("fills an empty question on a tracked page", async () => {
    buildGreenhouseForm(QUESTION);
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const reply = (await harness.send({ type: "FILL_FIELD", fieldName: "question_1", value: "draft" })) as FillFieldResult;

    expect(reply).toEqual({ filled: true });
    expect(value("#question_1")).toBe("draft");
  });

  it("reports D5's 'not_empty' distinctly and leaves hand-typed text alone", async () => {
    buildGreenhouseForm(QUESTION);
    document.querySelector<HTMLTextAreaElement>("#question_1")!.value = "my own answer";
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const reply = (await harness.send({ type: "FILL_FIELD", fieldName: "question_1", value: "LLM draft" })) as FillFieldResult;

    expect(reply).toEqual({ filled: false, reason: "not_empty" });
    expect(value("#question_1")).toBe("my own answer");
  });

  it("Lever still needs a verified field map", async () => {
    buildLeverForm(`
      <div><div class="application-label">Why us?</div>
      <div class="application-field"><input type="text" name="cards[q1][field0]" /></div></div>`);
    const noMap = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState({ fieldMap: null })) });
    expect(await noMap.send({ type: "FILL_FIELD", fieldName: "cards[q1][field0]", value: "draft" })).toEqual({
      filled: false,
      reason: "refused",
    });
    expect(value('input[name="cards[q1][field0]"]')).toBe("");

    const withMap = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState({ fieldMap: LEVER_MAP })) });
    expect(await withMap.send({ type: "FILL_FIELD", fieldName: "cards[q1][field0]", value: "draft" })).toEqual({ filled: true });
  });
});

// ---- the cover-letter slot is looked up as a FILE input ------------------------

describe("cover-letter attach only targets a file input", () => {
  it("skips a same-named text input that precedes the real file input", async () => {
    buildLeverForm(`
      <input type="text" name="cards[cl1][field0]" />
      <div><div class="application-label">Cover Letter</div>
      <div class="application-field"><input type="file" name="cards[cl1][field0]" /></div></div>`);
    const decoy = document.querySelector<HTMLInputElement>('input[type="text"][name="cards[cl1][field0]"]')!;
    const real = document.querySelector<HTMLInputElement>('input[type="file"][name="cards[cl1][field0]"]')!;
    // jsdom's own `files` setter rejects the test double FileList; make it a plain property (see lever.test.ts).
    Object.defineProperty(real, "files", { value: undefined, writable: true, configurable: true });

    const state = trackedState({
      fieldMap: LEVER_MAP,
      coverLetter: { base64: btoa("%PDF-1.4 fake"), filename: "cover-letter.pdf" },
    });
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(state) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply?.coverLetterAttached).toBe(true);
    expect(reply?.coverLetterError).toBeNull();
    expect(real.files?.length).toBe(1);
    expect(decoy.value).toBe("");
    void PERSON;
  });
});

describe("robustness", () => {
  it("survives the service worker answering nothing for PAGE_DETECTED", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: () => undefined });

    const reply = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;

    expect(reply.formDetected).toBe(true);
    expect(reply.tabState).toBeNull();
    await settle();
  });
});
