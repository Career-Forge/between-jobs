// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle } from "../testing/forms";
import { render } from "../testing/render";
import { useGeneration, type GenerationController } from "./useGeneration";

vi.mock("./api", async () => {
  const fake = await import("../testing/fakeApi");
  return { ...fake.apiModule, apiFetchBlob: async (path: string) => (fake.calls.push({ method: "GET", path, body: undefined }), new Blob(["pdf"])) };
});

// The generation hook, driven through a probe that hands its controller to the test.

let latest: GenerationController;
function Probe({ hasResume = false, id = "a1" }: { hasResume?: boolean; id?: string }) {
  latest = useGeneration(id, hasResume);
  return null;
}

const RESULT = { run_id: "r1", resume: { artifact_id: "a", version_id: "v", media_type: "application/pdf", sha256: "00" }, ats_attempts: [], final_score: null, score_scale: "ats_100", warnings: [] };

beforeEach(() => {
  reset();
  URL.createObjectURL = () => "blob:test";
  URL.revokeObjectURL = () => undefined;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  vi.spyOn(window.crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000000");
});
afterEach(() => vi.restoreAllMocks());

describe("reading the stored result", () => {
  it("reads it once on mount and restores it as restored", async () => {
    on("GET /applications/a1/prepare-result", { result: RESULT });
    render(<Probe />);
    expect(latest.read).toBe("loading");
    await settle();
    expect(latest.read).toBe("done");
    expect(latest.state).toEqual({ kind: "ready", result: RESULT, restored: true });
    expect(latest.hasResume).toBe(true);
    expect(callsTo("GET /applications/a1/prepare-result")).toHaveLength(1);
  });

  it("leaves nothing in place of a result that is null, and says the read is over", async () => {
    on("GET /applications/a1/prepare-result", { result: null });
    render(<Probe />);
    await settle();
    expect(latest.state).toEqual({ kind: "idle" });
    expect(latest.read).toBe("done");
  });

  it("says a failed read failed, keeps nothing, and reads again on retry", async () => {
    on("GET /applications/a1/prepare-result", new FakeApiError("no", "INTERNAL"));
    render(<Probe />);
    await settle();
    expect(latest.read).toBe("failed");
    expect(latest.state).toEqual({ kind: "idle" });
    on("GET /applications/a1/prepare-result", { result: RESULT });
    latest.retryRead();
    await settle();
    expect(latest.read).toBe("done");
    expect(latest.state.kind).toBe("ready");
  });

  it("does not let a stored result replace a run that was started while it was out", async () => {
    let release: (value: unknown) => void = () => undefined;
    on("GET /applications/a1/prepare-result", () => new Promise((resolve) => (release = resolve)));
    on("POST /applications/a1/prepare", { ...RESULT, run_id: "fresh" });
    render(<Probe />);
    await settle(1);
    void latest.generate();
    await settle();
    release({ result: { ...RESULT, run_id: "stored" } });
    await settle();
    expect(latest.state).toMatchObject({ kind: "ready", restored: false, result: { run_id: "fresh" } });
  });
});

describe("a run", () => {
  it("posts the same body as ever: an idempotency key, the force flag and the cover letter choice", async () => {
    on("GET /applications/a1/prepare-result", { result: null });
    on("POST /applications/a1/prepare", RESULT);
    render(<Probe />);
    await settle();
    latest.setWantCoverLetter(true);
    await settle(1);
    await latest.generate(true);
    await settle();
    expect(callsTo("POST /applications/a1/prepare")[0].body).toEqual({ idempotency_key: "00000000-0000-4000-8000-000000000000", force_generate: true, generate_cover_letter: true });
    expect(latest.state).toMatchObject({ kind: "ready", restored: false });
  });

  it("is one at a time: a second press while one is out sends nothing", async () => {
    on("GET /applications/a1/prepare-result", { result: null });
    on("POST /applications/a1/prepare", () => new Promise(() => undefined));
    render(<Probe />);
    await settle();
    void latest.generate();
    await settle(1);
    expect(latest.state.kind).toBe("generating");
    await latest.generate();
    expect(callsTo("POST /applications/a1/prepare")).toHaveLength(1);
  });

  it("keeps a setup failure apart from a failed run, and stamps the time on a failed run", async () => {
    on("GET /applications/a1/prepare-result", { result: null });
    on("POST /applications/a1/prepare", new FakeApiError("Add a model key.", "SETUP_REQUIRED"));
    render(<Probe />);
    await settle();
    await latest.generate();
    await settle();
    expect(latest.state.kind).toBe("setup");
    on("POST /applications/a1/prepare", new FakeApiError("The engine did not answer.", "INTERNAL"));
    await latest.generate();
    await settle();
    expect(latest.state).toMatchObject({ kind: "error", message: "The engine did not answer." });
    expect((latest.state as { at: string }).at).toMatch(/^\d\d:\d\d$/);
  });

  it("clears the export checks and the downloaded mark when a new run starts", async () => {
    on("GET /applications/a1/prepare-result", { result: RESULT });
    on("GET /applications/a1/export-checklist", { items: [] });
    on("POST /applications/a1/prepare", RESULT);
    render(<Probe />);
    await settle();
    await latest.downloadResume();
    await latest.loadChecklist();
    await settle();
    expect(latest.downloaded).toBe(true);
    expect(latest.checklist.kind).toBe("ready");
    await latest.generate();
    await settle();
    expect(latest.downloaded).toBe(false);
    expect(latest.checklist).toEqual({ kind: "idle" });
  });
});

describe("an answer that arrives late", () => {
  it("is dropped when it is the export checks of a resume a new generation has since replaced", async () => {
    on("GET /applications/a1/prepare-result", { result: RESULT });
    let answerChecks: (value: unknown) => void = () => undefined;
    on("GET /applications/a1/export-checklist", () => new Promise((resolve) => (answerChecks = resolve)));
    let finishRun: (value: unknown) => void = () => undefined;
    on("POST /applications/a1/prepare", () => new Promise((resolve) => (finishRun = resolve)));
    render(<Probe />);
    await settle();
    const asked = latest.loadChecklist();
    await settle(1);
    expect(latest.checklist.kind).toBe("loading");
    const run = latest.generate();
    await settle(1);
    expect(latest.checklist).toEqual({ kind: "idle" });
    answerChecks({ items: [{ key: "one_page", label: "One page", status: "pass", detail: "1 page." }] });
    await asked;
    await settle();
    expect(latest.checklist).toEqual({ kind: "idle" });
    finishRun(RESULT);
    await run;
    await settle();
    expect(latest.checklist).toEqual({ kind: "idle" });
  });

  it("lets only the newest ask for the export checks write its answer", async () => {
    on("GET /applications/a1/prepare-result", { result: RESULT });
    const answers: ((value: unknown) => void)[] = [];
    on("GET /applications/a1/export-checklist", () => new Promise((resolve) => answers.push(resolve)));
    render(<Probe />);
    await settle();
    const first = latest.loadChecklist();
    const second = latest.loadChecklist();
    await settle(1);
    answers[1]({ items: [{ key: "k", label: "Newer", status: "pass", detail: "" }] });
    await second;
    answers[0]({ items: [{ key: "k", label: "Older", status: "fail", detail: "" }] });
    await first;
    await settle();
    expect(latest.checklist).toEqual({ kind: "ready", items: [{ key: "k", label: "Newer", status: "pass", detail: "" }] });
  });
});

describe("a result the page could not show", () => {
  it("is told apart from none stored: a failed read is unreadable, a read that answered null is not", async () => {
    on("GET /applications/a1/prepare-result", new FakeApiError("no", "INTERNAL"));
    const failed = render(<Probe hasResume />);
    await settle();
    expect(latest.resultUnreadable).toBe(true);
    failed.unmount();
    on("GET /applications/a1/prepare-result", { result: null });
    render(<Probe hasResume />);
    await settle();
    expect(latest.resultUnreadable).toBe(false);
  });

  it("is unreadable when a failed or setup-blocked run dropped the result that was on screen, until a result is on screen again", async () => {
    on("GET /applications/a1/prepare-result", { result: RESULT });
    on("POST /applications/a1/prepare", new FakeApiError("The engine did not answer.", "INTERNAL"));
    render(<Probe hasResume />);
    await settle();
    expect(latest.resultUnreadable).toBe(false);
    await latest.generate();
    await settle();
    expect(latest.state.kind).toBe("error");
    expect(latest.resultUnreadable).toBe(true);
    on("POST /applications/a1/prepare", new FakeApiError("Add a model key.", "SETUP_REQUIRED"));
    await latest.generate();
    await settle();
    expect(latest.state.kind).toBe("setup");
    expect(latest.resultUnreadable).toBe(true);
    on("POST /applications/a1/prepare", RESULT);
    await latest.generate();
    await settle();
    expect(latest.resultUnreadable).toBe(false);
  });

  it("is not unreadable when the read answered with nothing and a run then failed: nothing was dropped", async () => {
    on("GET /applications/a1/prepare-result", { result: null });
    on("POST /applications/a1/prepare", new FakeApiError("Add a model key.", "SETUP_REQUIRED"));
    render(<Probe hasResume />);
    await settle();
    await latest.generate();
    await settle();
    expect(latest.state.kind).toBe("setup");
    expect(latest.resultUnreadable).toBe(false);
  });
});

describe("downloads and the export checks", () => {
  it("fetches a PDF from the right path, marks a resume download in this visit, and not a cover letter", async () => {
    on("GET /applications/a1/prepare-result", { result: { ...RESULT, cover_letter: RESULT.resume } });
    render(<Probe />);
    await settle();
    await latest.downloadCoverLetter();
    expect(callsTo("GET /applications/a1/cover-letter.pdf")).toHaveLength(1);
    expect(latest.downloaded).toBe(false);
    await latest.downloadResume();
    expect(callsTo("GET /applications/a1/resume.pdf")).toHaveLength(1);
    await settle();
    expect(latest.downloaded).toBe(true);
    expect(latest.hasCoverLetter).toBe(true);
  });

  it("does not run the export checks until asked, and names a failure", async () => {
    on("GET /applications/a1/prepare-result", { result: RESULT });
    on("GET /applications/a1/export-checklist", new FakeApiError("The server hit a problem.", "INTERNAL"));
    render(<Probe />);
    await settle();
    expect(callsTo("GET /applications/a1/export-checklist")).toHaveLength(0);
    await latest.loadChecklist();
    await settle();
    expect(latest.checklist).toEqual({ kind: "error", message: "The server hit a problem." });
  });

  it("has a resume when the page said so, even with no result", async () => {
    on("GET /applications/a1/prepare-result", { result: null });
    render(<Probe hasResume />);
    await settle();
    expect(latest.hasResume).toBe(true);
    expect(latest.hasCoverLetter).toBe(false);
  });
});
