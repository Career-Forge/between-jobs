import { afterEach, describe, expect, it, vi } from "vitest";
import type { AtsFieldMap } from "@/lib/ats-field-map";
import type { DetectionStateResponse, FillFieldResult, FillResult, TabState } from "@/lib/types";
import { CONSENT_VERSION } from "@/lib/consent";
import { LEVER_MAP, loadContentScript, NO_CONSENT, PERSON, settle, trackedState } from "./helpers/contentHarness";

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

// ---- E6 continuation: résumé/cover-letter PDFs are fetched lazily, on Fill ----

describe("résumé/cover-letter PDFs are fetched on Fill, not on detection", () => {
  function respondWithFiles(
    overrides: {
      resume?: { base64: string; filename: string } | null;
      resumeError?: string | null;
      coverLetter?: { base64: string; filename: string } | null;
      coverLetterError?: string | null;
    },
    prepareResult: {
      resume?: { artifact_id: string; version_id: string } | null;
      cover_letter?: { artifact_id: string; version_id: string } | null;
    } = { resume: { artifact_id: "a", version_id: "v" } },
  ) {
    return (message: { type: string; [key: string]: unknown }): unknown => {
      if (message.type === "PAGE_DETECTED") {
        return trackedState({
          payload: {
            prepare_result: prepareResult,
            personal_info: PERSON,
          },
        });
      }
      if (message.type === "VERIFY_SESSION") return { valid: true };
      if (message.type === "FETCH_APPLICATION_FILES") {
        return {
          resume: overrides.resume ?? null,
          resumeError: overrides.resumeError ?? null,
          coverLetter: overrides.coverLetter ?? null,
          coverLetterError: overrides.coverLetterError ?? null,
        };
      }
      return undefined;
    };
  }

  it("does not ask for the files at PAGE_DETECTED/GET_DETECTION_STATE time", async () => {
    buildLeverForm();
    const harness = await loadContentScript({
      href: LEVER_URL,
      respond: respondWithFiles({ resume: { base64: btoa("%PDF resume"), filename: "resume.pdf" } }),
    });
    await harness.send({ type: "GET_DETECTION_STATE" });

    expect(harness.sentOfType("FETCH_APPLICATION_FILES")).toHaveLength(0);
  });

  it("fetches the résumé the first time Fill is requested, and attaches it", async () => {
    buildLeverForm();
    // jsdom's own `files` setter rejects the test double FileList (a
    // documented jsdom gap, not this project's code -- see lever.test.ts's
    // own "attachFile" test and tests/setup.ts).
    const resumeInput = document.querySelector<HTMLInputElement>('input[name="resume"]')!;
    Object.defineProperty(resumeInput, "files", { value: undefined, writable: true, configurable: true });
    const harness = await loadContentScript({
      href: LEVER_URL,
      respond: respondWithFiles({ resume: { base64: btoa("%PDF resume"), filename: "resume.pdf" } }),
    });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(harness.sentOfType("FETCH_APPLICATION_FILES")).toEqual([
      { type: "FETCH_APPLICATION_FILES", applicationId: "app-A", wantResume: true, wantCoverLetter: false },
    ]);
    expect(reply?.resumeAttached).toBe(true);
    expect(resumeInput.files?.length).toBe(1);
  });

  it("a second Fill for the same application reuses the cached file instead of re-fetching it", async () => {
    buildLeverForm();
    const resumeInput = document.querySelector<HTMLInputElement>('input[name="resume"]')!;
    Object.defineProperty(resumeInput, "files", { value: undefined, writable: true, configurable: true });
    const harness = await loadContentScript({
      href: LEVER_URL,
      respond: respondWithFiles({ resume: { base64: btoa("%PDF resume"), filename: "resume.pdf" } }),
    });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.send({ type: "REQUEST_FILL", forceRefillAll: true });

    expect(harness.sentOfType("FETCH_APPLICATION_FILES")).toHaveLength(1);
  });

  it("surfaces a real fetch failure as resumeError, rather than the generic 'never generated' message", async () => {
    buildLeverForm();
    const harness = await loadContentScript({
      href: LEVER_URL,
      respond: respondWithFiles({ resumeError: "network hiccup" }),
    });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply?.resumeAttached).toBe(false);
    expect(reply?.resumeError).toBe("network hiccup");
  });

  it("an application with no résumé/cover letter at all never sends FETCH_APPLICATION_FILES", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });

    expect(harness.sentOfType("FETCH_APPLICATION_FILES")).toHaveLength(0);
  });

  it("a resume-only application (cover_letter: null, the real default shape) never asks for a cover letter -- on this Fill or any later one", async () => {
    // Regression for the real wire shape: `generate_cover_letter` defaults
    // to false, so the backend's actual GET /extension-payload response for
    // most applications is `prepare_result: { resume: {...}, cover_letter:
    // null }` -- the key is PRESENT with a JSON null value, not absent.
    // `wantCoverLetter` must derive from that null meaning "doesn't exist,"
    // not "not fetched yet," or every Fill re-requests (and re-404s on) a
    // cover letter that was never generated.
    buildLeverForm();
    const resumeInput = document.querySelector<HTMLInputElement>('input[name="resume"]')!;
    Object.defineProperty(resumeInput, "files", { value: undefined, writable: true, configurable: true });
    const harness = await loadContentScript({
      href: LEVER_URL,
      respond: respondWithFiles(
        { resume: { base64: btoa("%PDF resume"), filename: "resume.pdf" } },
        { resume: { artifact_id: "a", version_id: "v" }, cover_letter: null },
      ),
    });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    expect(harness.sentOfType("FETCH_APPLICATION_FILES")).toEqual([
      { type: "FETCH_APPLICATION_FILES", applicationId: "app-A", wantResume: true, wantCoverLetter: false },
    ]);

    // A later Fill (e.g. "Refill all") must not re-ask either -- proves
    // this isn't a one-shot fluke of the first check.
    await harness.send({ type: "REQUEST_FILL", forceRefillAll: true });
    expect(harness.sentOfType("FETCH_APPLICATION_FILES")).toHaveLength(1);
  });
});

// ---- E6 continuation: the per-question "Replace" action's force flag ----------

describe("FILL_FIELD's force flag", () => {
  const QUESTION = `<label for="question_1">Why us?</label><textarea id="question_1"></textarea>`;

  it("force:true overwrites a field that already has text", async () => {
    buildGreenhouseForm(QUESTION);
    document.querySelector<HTMLTextAreaElement>("#question_1")!.value = "my own answer";
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const reply = (await harness.send({
      type: "FILL_FIELD",
      fieldName: "question_1",
      value: "Replacement draft",
      force: true,
    })) as FillFieldResult;

    expect(reply).toEqual({ filled: true });
    expect(value("#question_1")).toBe("Replacement draft");
  });

  it("omitting force still refuses to overwrite -- the default is unchanged", async () => {
    buildGreenhouseForm(QUESTION);
    document.querySelector<HTMLTextAreaElement>("#question_1")!.value = "my own answer";
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const reply = (await harness.send({ type: "FILL_FIELD", fieldName: "question_1", value: "draft" })) as FillFieldResult;

    expect(reply).toEqual({ filled: false, reason: "not_empty" });
    expect(value("#question_1")).toBe("my own answer");
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


// ---- A: the consent gate -------------------------------------------------------
//
// Until the person has agreed to the CURRENT disclosure in the side panel, the
// content script reads nothing from the page (not even whether a form is there),
// sends no address to the service worker, and fills nothing. Withdrawing or
// re-versioning the consent closes the gate again in a tab that is already open.

describe("the consent gate", () => {
  // "Reads nothing from the page" is pinned at the level of the DOM's own read APIs, not of two
  // query methods: a read through any of them -- a query on the document or on an element, an
  // attribute, text, a form's fields, a field's value, the title -- is recorded by name. A
  // regression that reads the page through some other API before the consent check would
  // otherwise slip past a spy on `querySelector` alone.
  const READ_API: Array<[string, object, string[], string[]]> = [
    [
      "Document",
      Document.prototype,
      ["querySelector", "querySelectorAll", "getElementById", "getElementsByTagName", "getElementsByClassName", "getElementsByName"],
      ["title", "body", "forms", "documentElement", "head", "all", "images", "links", "cookie", "activeElement"],
    ],
    [
      "Element",
      Element.prototype,
      ["querySelector", "querySelectorAll", "closest", "matches", "getAttribute", "getAttributeNames", "getElementsByTagName", "getElementsByClassName"],
      ["innerHTML", "outerHTML", "children", "firstElementChild"],
    ],
    ["Node", Node.prototype, [], ["textContent", "childNodes", "firstChild", "parentElement"]],
    ["HTMLInputElement", HTMLInputElement.prototype, [], ["value", "checked", "files"]],
    ["HTMLTextAreaElement", HTMLTextAreaElement.prototype, [], ["value"]],
    ["HTMLFormElement", HTMLFormElement.prototype, [], ["elements"]],
  ];

  /** Runs `scenario` with every read API above recorded, and always puts them back. Returns the
   * distinct names that were called, so a failure names the API that read the page. */
  async function underPageReadWatch(scenario: () => Promise<void>): Promise<string[]> {
    const calls: string[] = [];
    const restorers: Array<() => void> = [];
    for (const [typeName, proto, methods, getters] of READ_API) {
      for (const name of [...methods, ...getters]) {
        const original = Object.getOwnPropertyDescriptor(proto, name);
        if (original === undefined) continue; // this DOM keeps it somewhere else
        const label = `${typeName}.${name}`;
        try {
          if (typeof original.value === "function") {
            const method = original.value as (...args: unknown[]) => unknown;
            Object.defineProperty(proto, name, {
              ...original,
              value: function (this: unknown, ...args: unknown[]) {
                calls.push(label);
                return method.apply(this, args);
              },
            });
          } else if (original.get !== undefined) {
            const getter = original.get;
            Object.defineProperty(proto, name, {
              ...original,
              get: function (this: unknown) {
                calls.push(label);
                return getter.call(this);
              },
            });
          } else {
            continue;
          }
          restorers.push(() => Object.defineProperty(proto, name, original));
        } catch {
          // A member this DOM will not let us wrap cannot be watched; the control test below
          // shows the watcher as a whole is live.
        }
      }
    }
    try {
      await scenario();
    } finally {
      for (const restore of restorers) restore();
    }
    return [...new Set(calls)];
  }

  const QUESTION = `<label for="question_1">Why us?</label><textarea id="question_1"></textarea>`;

  /** Everything the panel or a navigation can ask of the script, one after another. */
  async function everyRequest(harness: Awaited<ReturnType<typeof loadContentScript>>): Promise<void> {
    await harness.send({ type: "GET_DETECTION_STATE" });
    await harness.send({ type: "RECHECK" });
    await harness.send({ type: "REQUEST_FILL", forceRefillAll: true });
    await harness.send({ type: "FILL_FIELD", fieldName: "question_1", value: "draft", force: true });
    harness.fireLocationChange();
    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");
    await harness.settle();
  }

  const NOT_GRANTED: Array<[string, unknown]> = [
    ["no stored flag", NO_CONSENT],
    ["an older version", { version: CONSENT_VERSION - 1 }],
    ["a newer version", { version: CONSENT_VERSION + 1 }],
    ["a malformed flag", { version: String(CONSENT_VERSION) }],
    ["a bare true", true],
  ];

  it.each(NOT_GRANTED)("with %s: nothing is read from the page, nothing is sent, nothing is written", async (_name, consent) => {
    buildGreenhouseForm(QUESTION);
    let sent: unknown[] = [];
    const reads = await underPageReadWatch(async () => {
      const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()), consent });
      await everyRequest(harness);
      // PAGE_CHANGED only tells the open panel to refresh itself: it carries nothing.
      sent = harness.sent.filter((m) => m.type !== "PAGE_CHANGED");
    });

    expect(reads).toEqual([]);
    expect(sent).toEqual([]);
    expect(value("#first_name")).toBe("");
    expect(value("#email")).toBe("");
    expect(value("#question_1")).toBe("");
  });

  it("control: with consent the same sequence does read the page, so the watcher is live", async () => {
    buildGreenhouseForm(QUESTION);
    const reads = await underPageReadWatch(async () => {
      const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });
      await everyRequest(harness);
    });

    expect(reads.length).toBeGreaterThan(0);
    expect(reads).toContain("Document.querySelector");
  });

  it("without consent: the panel is told so, and nothing more", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()), consent: NO_CONSENT });
    const state = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;
    await harness.settle();

    expect(harness.sent).toEqual([]);
    expect(state).toEqual({ formDetected: false, tabState: { status: "consent_required" } });
  });

  it("without consent: RECHECK, REQUEST_FILL and FILL_FIELD answer as refused", async () => {
    buildGreenhouseForm(QUESTION);
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()), consent: NO_CONSENT });

    const recheck = await harness.send({ type: "RECHECK" });
    const fill = await harness.send({ type: "REQUEST_FILL", forceRefillAll: true });
    const answer = await harness.send({ type: "FILL_FIELD", fieldName: "question_1", value: "draft", force: true });
    await harness.settle();

    expect(recheck).toEqual({ formDetected: false, tabState: { status: "consent_required" } });
    expect(fill).toBeNull();
    expect(answer).toEqual({ filled: false, reason: "refused" });
    expect(harness.sent).toEqual([]);
  });

  it("a client-side navigation without consent still looks nothing up", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()), consent: NO_CONSENT });

    harness.fireLocationChange();
    harness.navigate("https://job-boards.greenhouse.io/acme/jobs/2");
    await harness.settle();

    expect(harness.sentOfType("PAGE_DETECTED")).toEqual([]);
  });

  it("a stale stored version is not consent", async () => {
    buildLeverForm();
    const harness = await loadContentScript({
      href: LEVER_URL,
      respond: backend(trackedState()),
      consent: { version: CONSENT_VERSION - 1 },
    });

    expect(harness.sentOfType("PAGE_DETECTED")).toEqual([]);
    expect(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false })).toBeNull();
    expect(value('input[name="name"]')).toBe("");
  });

  it("with consent: looks the page up at load and fills as before", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });

    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(1);
    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
    expect(reply).not.toBeNull();
    expect(value('input[name="name"]')).toBe("Alice Example");
  });

  it("consent withdrawn mid-session closes every entry point (the dropping itself is pinned below)", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });
    expect(fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }))).not.toBeNull();
    const lookupsBefore = harness.sentOfType("PAGE_DETECTED").length;
    document.querySelectorAll<HTMLInputElement>("input[type=text],input[type=email]").forEach((i) => (i.value = ""));

    harness.setConsent(undefined); // withdrawn

    const state = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;
    expect(state).toEqual({ formDetected: false, tabState: { status: "consent_required" } });
    expect(await harness.send({ type: "REQUEST_FILL", forceRefillAll: true })).toBeNull();
    expect(await harness.send({ type: "RECHECK" })).toEqual({
      formDetected: false,
      tabState: { status: "consent_required" },
    });
    await harness.settle();
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(lookupsBefore);
    expect(value('input[name="name"]')).toBe("");
    expect(value('input[name="email"]')).toBe("");
  });

  it("closes without waiting for the storage event: the next request re-reads the flag", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });
    expect(fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }))).not.toBeNull();
    document.querySelectorAll<HTMLInputElement>("input[type=text],input[type=email]").forEach((i) => (i.value = ""));

    harness.setConsent(undefined, { notify: false });

    expect(await harness.send({ type: "REQUEST_FILL", forceRefillAll: true })).toBeNull();
    expect(value('input[name="name"]')).toBe("");
  });

  it("a withdrawal lands before a lookup that was still in flight: its answer is discarded", async () => {
    buildLeverForm();
    let release: (state: TabState) => void = () => {};
    const slow = new Promise<TabState>((resolve) => {
      release = resolve;
    });
    const respond = (message: { type: string; [key: string]: unknown }): unknown =>
      message.type === "PAGE_DETECTED" ? slow : message.type === "VERIFY_SESSION" ? { valid: true } : undefined;
    const harness = await loadContentScript({ href: LEVER_URL, respond });

    harness.setConsent(undefined);
    release(trackedState());
    await harness.settle();

    expect(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false })).toBeNull();
    expect(value('input[name="name"]')).toBe("");
  });

  it("a consent version bump closes the gate in an already-open tab", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });
    expect(fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }))).not.toBeNull();
    document.querySelectorAll<HTMLInputElement>("input[type=text],input[type=email]").forEach((i) => (i.value = ""));

    harness.setConsent({ version: CONSENT_VERSION + 1 });

    expect(await harness.send({ type: "REQUEST_FILL", forceRefillAll: true })).toBeNull();
    expect(await harness.send({ type: "GET_DETECTION_STATE" })).toEqual({
      formDetected: false,
      tabState: { status: "consent_required" },
    });
    expect(value('input[name="name"]')).toBe("");
  });

  it("agreeing later opens the gate in the same open tab, without a reload", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()), consent: NO_CONSENT });
    expect(harness.sent).toEqual([]);

    harness.setConsent({ version: CONSENT_VERSION });
    const state = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;

    expect(state.formDetected).toBe(true);
    expect(state.tabState?.status).toBe("tracked");
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(1);
  });

  // What the content script holds for the page (the profile details and the files) is dropped
  // when consent stops being valid -- by the storage event, and by each entry point's own read
  // -- so agreeing again starts from a fresh lookup, never from what was held before.
  it("the storage event drops what was held: after a withdrawal and a silent re-grant the next ask looks up again", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(1);

    harness.setConsent(undefined); // withdrawn, with the event
    harness.setConsent({ version: CONSENT_VERSION }, { notify: false }); // agreed again, no event
    const state = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;

    expect(state.tabState?.status).toBe("tracked");
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(2);
  });

  it("each entry point drops what was held when it finds the flag gone, with no event at all", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });

    harness.setConsent(undefined, { notify: false });
    expect(await harness.send({ type: "GET_DETECTION_STATE" })).toEqual({
      formDetected: false,
      tabState: { status: "consent_required" },
    });
    harness.setConsent({ version: CONSENT_VERSION }, { notify: false });
    await harness.send({ type: "GET_DETECTION_STATE" });

    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(2);
  });

  it("the state is also refused at the moment it is read: a flag that goes between the two reads of one request", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });
    let reads = 0;
    vi.stubGlobal("chrome", {
      storage: {
        local: {
          // The first read of the request (the entry point's) sees the flag; every later one does not.
          get: async (key: string) => (reads++ === 0 ? { [key]: { version: CONSENT_VERSION } } : {}),
        },
        onChanged: { addListener: () => {} },
      },
    });

    const state = (await harness.send({ type: "GET_DETECTION_STATE" })) as DetectionStateResponse;

    expect(state.tabState?.status).not.toBe("tracked");
    expect(reads).toBeGreaterThan(1);
  });

  it("an unreadable flag is no consent", async () => {
    buildLeverForm();
    const harness = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState()) });
    vi.stubGlobal("chrome", {
      storage: {
        local: {
          get: async () => {
            throw new Error("storage unavailable");
          },
        },
        onChanged: { addListener: () => {} },
      },
    });

    expect(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false })).toBeNull();
  });
});

// ---- D: adapter gaps, seen from the fill the panel receives ----------------------------------

describe("D: what the fill reports for the adapter gaps", () => {
  const withPerson = (overrides: Partial<typeof PERSON>, extra: Parameters<typeof trackedState>[0] = {}) =>
    trackedState({ payload: { prepare_result: null, personal_info: { ...PERSON, ...overrides } }, ...extra });

  it("Greenhouse: a one-word name fills the first box, leaves the last empty and says so", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(withPerson({ name: "Madonna" })) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(value("#first_name")).toBe("Madonna");
    expect(value("#last_name")).toBe("");
    expect(reply!.skippedFields).toEqual(["Last name: not filled -- your profile name has only one part."]);
  });

  it("Greenhouse: #country as a plain select is filled from the profile and counted", async () => {
    buildGreenhouseForm(`<select id="country"><option value="">Select...</option><option value="US">United States</option></select>`);
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(document.querySelector<HTMLSelectElement>("#country")!.value).toBe("US");
    expect(reply!.filledFields).toContain("#country");
    expect(reply!.skippedFields).toEqual([]);
  });

  it("Greenhouse: #country that cannot be filled is reported as 'not filled: reason', not skipped silently", async () => {
    buildGreenhouseForm(`<input type="text" id="country" />`);
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply!.filledFields).not.toContain("#country");
    expect(reply!.skippedFields).toHaveLength(1);
    expect(reply!.skippedFields[0]).toMatch(/^Country: not filled -- /);
  });

  it("Ashby: a phone box found by type is filled, and is no longer listed as an unanswered question", async () => {
    buildAshbyForm(`
      <div data-field-path="q-phone"><label class="ashby-application-form-question-title" for="q-phone">Phone number</label>
      <input type="tel" id="q-phone" name="q-phone" /></div>`);
    const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState()) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(value("#q-phone")).toBe("+1-555-0100");
    expect(reply!.filledFields).toContain("#q-phone");
    expect(reply!.unresolvedQuestions.map((q) => q.fieldName)).not.toContain("q-phone");
  });

  it("Greenhouse: a country already chosen is left alone, and replaced only on Refill all", async () => {
    buildGreenhouseForm(
      `<select id="country"><option value="">Select...</option><option value="IN" selected>India</option><option value="US">United States</option></select>`,
    );
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const normal = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
    expect(document.querySelector<HTMLSelectElement>("#country")!.value).toBe("IN");
    expect(normal!.filledFields).not.toContain("#country");

    const forced = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: true }));
    expect(document.querySelector<HTMLSelectElement>("#country")!.value).toBe("US");
    expect(forced!.filledFields).toContain("#country");
  });

  describe("Ashby: a question that only mentions a phone", () => {
    const MENTIONS = `<div data-field-path="q-1"><label class="ashby-application-form-question-title" for="q-1">How many years of mobile development experience do you have?</label>
      <input type="text" id="q-1" name="q-1" /></div>`;

    it("is not given the number, stays listed for the person, and keeps what they typed on Refill all", async () => {
      buildAshbyForm(MENTIONS);
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState()) });

      const first = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
      expect(value("#q-1")).toBe("");
      expect(first!.filledFields).not.toContain("#q-1");
      expect(first!.unresolvedQuestions.map((q) => q.fieldName)).toContain("q-1");

      document.querySelector<HTMLInputElement>("#q-1")!.value = "6 years";
      const again = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: true }));
      expect(value("#q-1")).toBe("6 years");
      expect(again!.unresolvedQuestions.map((q) => q.fieldName)).toContain("q-1");
    });

    it("beside a real tel box: the tel box is filled and the question is left alone", async () => {
      buildAshbyForm(
        `<div data-field-path="q-phone"><label class="ashby-application-form-question-title" for="q-phone">Phone</label><input type="tel" id="q-phone" name="q-phone" /></div>` +
          MENTIONS,
      );
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState()) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(value("#q-phone")).toBe("+1-555-0100");
      expect(value("#q-1")).toBe("");
      expect(reply!.unresolvedQuestions.map((q) => q.fieldName)).toEqual(["q-1"]);
    });
  });

  it("Ashby: a phone box the profile has no number for is listed for the person, not silently dropped", async () => {
    buildAshbyForm(`<div data-field-path="q-phone"><label class="ashby-application-form-question-title" for="q-phone">Phone</label>
      <input type="tel" id="q-phone" name="q-phone" /></div>`);
    const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(withPerson({ phone: null })) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(value("#q-phone")).toBe("");
    expect(reply!.unresolvedQuestions).toEqual([{ fieldName: "q-phone", label: "Phone", kind: "text" }]);
  });

  it("Ashby: two boxes that could both be the phone are left empty and reported", async () => {
    buildAshbyForm(`
      <div data-field-path="q-a"><label class="ashby-application-form-question-title" for="q-a">Phone</label><input type="tel" id="q-a" name="q-a" /></div>
      <div data-field-path="q-b"><label class="ashby-application-form-question-title" for="q-b">Mobile phone</label><input type="tel" id="q-b" name="q-b" /></div>`);
    const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState()) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(value("#q-a")).toBe("");
    expect(value("#q-b")).toBe("");
    expect(reply!.skippedFields).toEqual(["Phone: not filled -- more than one box on this form could be your phone number."]);
  });

  describe("Ashby cover letter", () => {
    const withCoverLetter = { cover_letter: { artifact_id: "a", version_id: "v" } };
    const slot = `
      <div data-field-path="cl-1"><label class="ashby-application-form-question-title" for="cl-1">Cover Letter</label>
      <input type="file" id="cl-1" name="cl-1" /></div>`;
    const coverState = (extra: Parameters<typeof trackedState>[0] = {}) =>
      trackedState({
        payload: { prepare_result: withCoverLetter, personal_info: PERSON },
        coverLetter: { base64: btoa("%PDF-1.4 fake"), filename: "cover-letter.pdf" },
        ...extra,
      });

    it("is attached to the upload whose own title says cover letter", async () => {
      buildAshbyForm(slot);
      const input = document.querySelector<HTMLInputElement>("#cl-1")!;
      Object.defineProperty(input, "files", { value: undefined, writable: true, configurable: true });
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(coverState()) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(reply!.coverLetterAttached).toBe(true);
      expect(reply!.coverLetterError).toBeNull();
      expect(input.files?.length).toBe(1);
      expect(reply!.unresolvedQuestions.map((q) => q.fieldName)).not.toContain("cl-1");
    });

    it("says why it was not attached when the form has no cover-letter upload (never silent)", async () => {
      buildAshbyForm();
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(coverState()) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(reply!.coverLetterAttached).toBe(false);
      expect(reply!.coverLetterError).toMatch(/Couldn't find a cover-letter upload/);
    });

    it("says so, and attaches nothing, when two uploads could be the cover letter", async () => {
      buildAshbyForm(
        slot +
          `<div data-field-path="cl-2"><label class="ashby-application-form-question-title" for="cl-2">Cover letter (other)</label><input type="file" id="cl-2" name="cl-2" /></div>`,
      );
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(coverState()) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(reply!.coverLetterAttached).toBe(false);
      expect(reply!.coverLetterError).toMatch(/more than one upload/);
    });

    it("an upload that already holds a file is kept, and replaced only on Refill all", async () => {
      buildAshbyForm(slot);
      const input = document.querySelector<HTMLInputElement>("#cl-1")!;
      Object.defineProperty(input, "files", { value: [new File(["old"], "old.pdf")], writable: true, configurable: true });
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(coverState()) });

      const kept = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
      expect(kept!.coverLetterAttached).toBe(true);
      expect(input.files?.[0]?.name).toBe("old.pdf");

      const replaced = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: true }));
      expect(replaced!.coverLetterAttached).toBe(true);
      expect(input.files?.[0]?.name).toBe("cover-letter.pdf");
    });

    it("a download that failed is the reason given for the slot, not the generic 'never generated'", async () => {
      buildAshbyForm(slot);
      const respond = (message: { type: string; [key: string]: unknown }): unknown => {
        if (message.type === "PAGE_DETECTED") {
          return trackedState({ payload: { prepare_result: withCoverLetter, personal_info: PERSON } });
        }
        if (message.type === "VERIFY_SESSION") return { valid: true };
        if (message.type === "FETCH_APPLICATION_FILES") {
          return { resume: null, resumeError: null, coverLetter: null, coverLetterError: "network hiccup" };
        }
        return undefined;
      };
      const harness = await loadContentScript({ href: ASHBY_URL, respond });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(reply!.coverLetterAttached).toBe(false);
      expect(reply!.coverLetterError).toBe("network hiccup");
    });

    it("a single-line text input titled cover letter is not a slot: the person is told, and the question stays listed", async () => {
      buildAshbyForm(
        `<div data-field-path="cl-text"><label class="ashby-application-form-question-title" for="cl-text">Cover letter</label><input type="text" id="cl-text" name="cl-text" /></div>`,
      );
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(coverState()) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(reply!.coverLetterAttached).toBe(false);
      expect(reply!.coverLetterError).toMatch(/Couldn't find a cover-letter upload/);
      expect(reply!.unresolvedQuestions.map((q) => q.fieldName)).toContain("cl-text");
    });

    it("says nothing about a cover letter the application never had, when the form has no slot", async () => {
      buildAshbyForm();
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState()) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(reply!.coverLetterError).toBeNull();
    });

    it("a slot with no cover letter generated says that, as Lever and Greenhouse do", async () => {
      buildAshbyForm(slot);
      const harness = await loadContentScript({ href: ASHBY_URL, respond: backend(trackedState()) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(reply!.coverLetterError).toBe("No cover letter was generated for this application yet.");
    });
  });

  describe("Lever: a 'portfolio or GitHub' box follows its own label", () => {
    const linkMap = (label: string): { form: string; map: typeof LEVER_MAP } => ({
      form: `<div><div class="application-label">${label}</div>
        <div class="application-field"><input type="text" name="urls[Other]" /></div></div>`,
      map: {
        ...LEVER_MAP,
        standard_fields: [
          { field: "links", selector: 'input[name="urls[Other]"]', strategy: "fallback", profileFields: ["portfolio", "github"] },
        ],
      },
    });
    const both = { portfolio: "https://alice.dev", github: "https://github.com/alice" };

    it("'GitHub URL' gets the GitHub link", async () => {
      const { form, map } = linkMap("GitHub URL");
      buildLeverForm(form);
      const harness = await loadContentScript({ href: LEVER_URL, respond: backend(withPerson(both, { fieldMap: map })) });

      await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });

      expect(value('input[name="urls[Other]"]')).toBe("https://github.com/alice");
    });

    it("'Other website' gets the portfolio", async () => {
      const { form, map } = linkMap("Other website");
      buildLeverForm(form);
      const harness = await loadContentScript({ href: LEVER_URL, respond: backend(withPerson(both, { fieldMap: map })) });

      await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });

      expect(value('input[name="urls[Other]"]')).toBe("https://alice.dev");
    });

    it("'GitHub URL' with a bare handle in the profile is left empty, and the reason is not 'has none'", async () => {
      const { form, map } = linkMap("GitHub URL");
      buildLeverForm(form);
      const harness = await loadContentScript({
        href: LEVER_URL,
        respond: backend(withPerson({ portfolio: "https://alice.dev", github: "alice" }, { fieldMap: map })),
      });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(value('input[name="urls[Other]"]')).toBe("");
      expect(reply!.skippedFields).toEqual([
        "GitHub link: not filled -- the form asks for a GitHub profile link, and no link in your profile is a github.com address.",
      ]);
    });

    it("'GitHub URL' with no GitHub link is left empty and reported", async () => {
      const { form, map } = linkMap("GitHub URL");
      buildLeverForm(form);
      const harness = await loadContentScript({
        href: LEVER_URL,
        respond: backend(withPerson({ portfolio: "https://alice.dev", github: "" }, { fieldMap: map })),
      });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

      expect(value('input[name="urls[Other]"]')).toBe("");
      expect(reply!.skippedFields).toEqual([
        "GitHub link: not filled -- the form asks for a GitHub link and your profile has none.",
      ]);
    });
  });
});

// ---- G: fill-outcome telemetry: counts only, only with consent, never in the fill's way -------

describe("G: fill-outcome report", () => {
  const reports = (harness: Awaited<ReturnType<typeof loadContentScript>>) => harness.sentOfType("REPORT_FILL_OUTCOME");
  const respondWith = (state: TabState, extra: (m: { type: string; [key: string]: unknown }) => unknown = () => undefined) =>
    (message: { type: string; [key: string]: unknown }): unknown => {
      if (message.type === "PAGE_DETECTED") return state;
      if (message.type === "VERIFY_SESSION") return { valid: true };
      return extra(message);
    };

  it("a fill that put everything in place reports exactly the counts and one outcome word", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.settle();

    expect(reports(harness)).toEqual([
      {
        type: "REPORT_FILL_OUTCOME",
        atsType: "greenhouse",
        applicationId: "app-A",
        fieldsAttempted: 4, // first_name, last_name, email, phone
        fieldsFilled: 4,
        outcome: "ok",
      },
    ]);
  });

  it("a fill that left a field empty on purpose reports it as tried and not filled: partial", async () => {
    buildGreenhouseForm();
    const state = trackedState({ payload: { prepare_result: null, personal_info: { ...PERSON, name: "Madonna" } } });
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(state) });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.settle();

    expect(reports(harness)).toEqual([
      expect.objectContaining({ fieldsAttempted: 4, fieldsFilled: 3, outcome: "partial" }),
    ]);
  });

  it("a fill whose only attempt failed reports failed", async () => {
    buildGreenhouseForm();
    const state = trackedState({
      payload: {
        prepare_result: { resume: { artifact_id: "a", version_id: "v" } },
        personal_info: { ...PERSON, name: "", email: null, phone: null },
      },
    });
    const harness = await loadContentScript({
      href: GREENHOUSE_URL,
      respond: respondWith(state, (m) =>
        m.type === "FETCH_APPLICATION_FILES"
          ? { resume: null, resumeError: "network hiccup", coverLetter: null, coverLetterError: null }
          : undefined,
      ),
    });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.settle();

    expect(reports(harness)).toEqual([
      expect.objectContaining({ atsType: "greenhouse", fieldsAttempted: 1, fieldsFilled: 0, outcome: "failed" }),
    ]);
  });

  it("the message carries no label, value, URL or page text -- only its six fields", async () => {
    buildGreenhouseForm(`<label for="question_1">Why do you want to work at Acme?</label><textarea id="question_1"></textarea>`);
    const harness = await loadContentScript({ href: `${GREENHOUSE_URL}?gh_src=secret-token`, respond: backend(trackedState()) });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.settle();

    const [report] = reports(harness);
    expect(Object.keys(report!).sort()).toEqual(
      ["applicationId", "atsType", "fieldsAttempted", "fieldsFilled", "outcome", "type"].sort(),
    );
    expect(JSON.stringify(report)).not.toMatch(/Alice|alice@example|555-0100|Acme|secret-token|greenhouse\.io|first_name/);
  });

  it("a Lever fill reports its own ATS: ok with a usable signed map, partial without one (part of the form was never tried)", async () => {
    buildLeverForm();
    const withMap = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState({ fieldMap: LEVER_MAP })) });
    await withMap.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await withMap.settle();
    expect(reports(withMap)[0]).toMatchObject({ atsType: "lever", outcome: "ok" });

    document.querySelectorAll<HTMLInputElement>("input[type=text],input[type=email]").forEach((i) => (i.value = ""));
    const withoutMap = await loadContentScript({ href: LEVER_URL, respond: backend(trackedState({ fieldMap: null })) });
    await withoutMap.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await withoutMap.settle();
    expect(reports(withoutMap)[0]).toMatchObject({ atsType: "lever", outcome: "partial" });
  });

  it("nothing is reported for a page the lookup could not resolve (untracked, signed out, error)", async () => {
    for (const state of [{ status: "untracked" }, { status: "signed_out" }, { status: "error", message: "Lookup failed" }] as TabState[]) {
      buildGreenhouseForm();
      const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(state) });

      await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
      await harness.settle();

      expect(reports(harness), state.status).toEqual([]);
    }
  });

  it("nothing is reported when the lookup never answered", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: () => undefined });

    expect(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false })).toBeNull();
    await harness.settle();

    expect(reports(harness)).toEqual([]);
  });

  it("nothing is reported without consent", async () => {
    buildGreenhouseForm();
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()), consent: NO_CONSENT });

    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.settle();

    expect(harness.sent).toEqual([]);
  });

  const withResume = () =>
    trackedState({ payload: { prepare_result: { resume: { artifact_id: "a", version_id: "v" } }, personal_info: PERSON } });

  it("consent withdrawn while the files were being fetched: nothing is written, nothing is reported, the profile is dropped", async () => {
    buildGreenhouseForm();
    let withdraw: () => void = () => {};
    const respond = (message: { type: string; [key: string]: unknown }): unknown => {
      if (message.type === "PAGE_DETECTED") return withResume();
      if (message.type === "VERIFY_SESSION") return { valid: true };
      if (message.type === "FETCH_APPLICATION_FILES") {
        withdraw(); // the person revokes in the panel while the files are being fetched
        return { resume: null, resumeError: null, coverLetter: null, coverLetterError: null };
      }
      return undefined;
    };
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond });
    withdraw = () => harness.setConsent(undefined, { notify: false });

    const reply = (await harness.send({ type: "REQUEST_FILL", forceRefillAll: false })) as FillResult | null;
    await harness.settle();

    expect(reply).toBeNull();
    expect(value("#first_name")).toBe("");
    expect(value("#email")).toBe("");
    expect(reports(harness)).toEqual([]);
    // What it was holding is gone: agreeing again starts from a fresh lookup.
    harness.setConsent({ version: CONSENT_VERSION }, { notify: false });
    await harness.send({ type: "GET_DETECTION_STATE" });
    expect(harness.sentOfType("PAGE_DETECTED")).toHaveLength(2);
  });

  it("consent withdrawn once the fill is already writing: it finishes the page it is on, and reports nothing", async () => {
    buildGreenhouseForm(`
      <div class="select__container"><div class="select__control"><div class="select__value-container">
        <div class="select__placeholder">Select...</div>
        <div class="select__input-container"><input id="country" type="text" role="combobox" aria-controls="lb" /></div>
      </div></div></div>`);
    let withdraw: () => void = () => {};
    const container = document.querySelector(".select__container")!;
    const country = document.querySelector<HTMLInputElement>("#country")!;
    country.addEventListener("input", () => {
      if (country.value === "") return;
      withdraw(); // the flag goes while the country list is being driven
      const listbox = document.createElement("div");
      listbox.id = "lb";
      listbox.setAttribute("role", "listbox");
      const option = document.createElement("div");
      option.setAttribute("role", "option");
      option.textContent = "United States";
      option.addEventListener("click", () => {
        container.querySelector(".select__placeholder")?.replaceWith(
          Object.assign(document.createElement("div"), { className: "select__single-value", textContent: "United States" }),
        );
        listbox.remove();
      });
      listbox.append(option);
      container.append(listbox);
    });
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });
    withdraw = () => harness.setConsent(undefined, { notify: false });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
    await harness.settle();

    expect(reply).not.toBeNull();
    expect(value("#first_name")).toBe("Alice");
    expect(reply!.filledFields).toContain("#country");
    expect(reports(harness)).toEqual([]);
  });

  // ---- a file that was already there is not a field this fill filled ----

  describe("a résumé or cover letter already on the form", () => {
    const PDF_BASE64 = btoa("%PDF-1.4 synthetic");
    const respondWithResume = (extra: Partial<Parameters<typeof trackedState>[0]> = {}) =>
      respondWith(trackedState({ payload: { prepare_result: withResume().payload.prepare_result, personal_info: PERSON }, ...extra }), (m) =>
        m.type === "FETCH_APPLICATION_FILES"
          ? { resume: { base64: PDF_BASE64, filename: "resume.pdf" }, resumeError: null, coverLetter: null, coverLetterError: null }
          : undefined,
      );
    const alreadyHolds = (input: HTMLInputElement) =>
      Object.defineProperty(input, "files", { value: [new File(["old"], "old.pdf")], writable: true, configurable: true });

    it("a repeat fill that finds every box typed and the résumé in place reports nothing", async () => {
      buildGreenhouseForm();
      for (const id of ["first_name", "last_name", "email", "phone"]) {
        document.querySelector<HTMLInputElement>(`#${id}`)!.value = "already typed";
      }
      alreadyHolds(document.querySelector<HTMLInputElement>("#resume")!);
      const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: respondWithResume() });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
      await harness.settle();

      expect(reply!.filledFields).toEqual([]);
      expect(reply!.resumeAttached).toBe(true); // the panel still says it is attached
      expect(reports(harness)).toEqual([]);
    });

    // The same on each service, with a cover letter too: files already in place are not a fill.
    const BOTH = { resume: { artifact_id: "a", version_id: "v" }, cover_letter: { artifact_id: "b", version_id: "w" } };
    const respondWithBoth = (extra: Parameters<typeof trackedState>[0] = {}) =>
      respondWith(trackedState({ payload: { prepare_result: BOTH, personal_info: PERSON }, ...extra }), (m) =>
        m.type === "FETCH_APPLICATION_FILES"
          ? {
              resume: { base64: PDF_BASE64, filename: "resume.pdf" },
              resumeError: null,
              coverLetter: { base64: PDF_BASE64, filename: "cover-letter.pdf" },
              coverLetterError: null,
            }
          : undefined,
      );

    it("Lever: nothing to write and a file already on each upload reports nothing", async () => {
      buildLeverForm(`<div><div class="application-label">Cover letter</div><div class="application-field"><input type="file" name="cards[cl][field0]" /></div></div>`);
      for (const name of ["name", "email", "phone", "location"]) {
        document.querySelector<HTMLInputElement>(`input[name="${name}"]`)!.value = "already typed";
      }
      for (const input of document.querySelectorAll<HTMLInputElement>('input[type="file"]')) alreadyHolds(input);
      const harness = await loadContentScript({ href: LEVER_URL, respond: respondWithBoth({ fieldMap: LEVER_MAP }) });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
      await harness.settle();

      expect(reply!.resumeAttached).toBe(true);
      expect(reply!.coverLetterAttached).toBe(true);
      expect(reports(harness)).toEqual([]);
    });

    it("Greenhouse: the same, with a cover letter", async () => {
      buildGreenhouseForm(`<input type="file" id="cover_letter" />`);
      for (const id of ["first_name", "last_name", "email", "phone"]) {
        document.querySelector<HTMLInputElement>(`#${id}`)!.value = "already typed";
      }
      for (const input of document.querySelectorAll<HTMLInputElement>('input[type="file"]')) alreadyHolds(input);
      const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: respondWithBoth() });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
      await harness.settle();

      expect(reply!.coverLetterAttached).toBe(true);
      expect(reports(harness)).toEqual([]);
    });

    it("Ashby: the same, with a cover letter", async () => {
      buildAshbyForm(`<div data-field-path="cl-1"><label class="ashby-application-form-question-title" for="cl-1">Cover Letter</label><input type="file" id="cl-1" name="cl-1" /></div>`);
      for (const id of ["_systemfield_name", "_systemfield_email"]) {
        document.querySelector<HTMLInputElement>(`#${id}`)!.value = "already typed";
      }
      for (const input of document.querySelectorAll<HTMLInputElement>('input[type="file"]')) alreadyHolds(input);
      const harness = await loadContentScript({ href: ASHBY_URL, respond: respondWithBoth() });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
      await harness.settle();

      expect(reply!.coverLetterAttached).toBe(true);
      expect(reports(harness)).toEqual([]);
    });

    it("a fresh fill that writes the boxes and attaches the résumé counts the résumé once", async () => {
      buildGreenhouseForm();
      Object.defineProperty(document.querySelector<HTMLInputElement>("#resume")!, "files", {
        value: undefined,
        writable: true,
        configurable: true,
      });
      const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: respondWithResume() });

      await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
      await harness.settle();

      expect(reports(harness)).toEqual([
        expect.objectContaining({ fieldsAttempted: 5, fieldsFilled: 5, outcome: "ok" }),
      ]);
    });

    it("a replacement that fails over an existing file is tried and not filled, so it is not ok", async () => {
      document.body.innerHTML = `<form><input type="file" id="resume" /></form>`;
      // A files property that refuses to be set: attaching throws, with the old file still there.
      Object.defineProperty(document.querySelector<HTMLInputElement>("#resume")!, "files", {
        get: () => [new File(["old"], "old.pdf")],
        configurable: true,
      });
      const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: respondWithResume() });

      const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: true }));
      await harness.settle();

      expect(reply!.resumeError).not.toBeNull();
      expect(reports(harness)).toEqual([
        expect.objectContaining({ fieldsAttempted: 1, fieldsFilled: 0, outcome: "failed" }),
      ]);
    });
  });

  it("nothing is reported for a fill that had nothing to try", async () => {
    buildGreenhouseForm();
    for (const id of ["first_name", "last_name", "email", "phone"]) {
      document.querySelector<HTMLInputElement>(`#${id}`)!.value = "already typed";
    }
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond: backend(trackedState()) });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
    await harness.settle();

    expect(reply!.filledFields).toEqual([]);
    expect(reports(harness)).toEqual([]);
  });

  it("a report that cannot be delivered changes nothing about the fill and surfaces no error", async () => {
    buildGreenhouseForm();
    const errors: unknown[][] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args) => void errors.push(args));
    const harness = await loadContentScript({
      href: GREENHOUSE_URL,
      respond: respondWith(trackedState(), (m) => {
        if (m.type === "REPORT_FILL_OUTCOME") throw new Error("service worker is gone");
        return undefined;
      }),
    });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));
    await harness.settle();

    expect(reports(harness)).toHaveLength(1); // it was attempted ...
    expect(reply!.filledFields).toHaveLength(4); // ... and the fill is exactly what it would have been
    expect(reply!.fillError).toBeNull();
    expect(value("#first_name")).toBe("Alice");
    expect(errors).toEqual([]);
    spy.mockRestore();
  });

  it("the reply to the panel does not wait for the report", async () => {
    buildGreenhouseForm();
    let release: (v: unknown) => void = () => {};
    const slow = new Promise((resolve) => {
      release = resolve;
    });
    const harness = await loadContentScript({
      href: GREENHOUSE_URL,
      respond: respondWith(trackedState(), (m) => (m.type === "REPORT_FILL_OUTCOME" ? slow : undefined)),
    });

    const reply = fillOf(await harness.send({ type: "REQUEST_FILL", forceRefillAll: false }));

    expect(reply!.filledFields).toHaveLength(4);
    release(undefined);
  });
});
