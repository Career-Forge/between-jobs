// @vitest-environment jsdom
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, calls, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { TailorPanel } from "./TailorPanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const DOCUMENT = { id: "doc-1", profile_version_id: "9f8e7d6c5b4a3210", selected_evidence_fact_ids: [] };
const COVERAGE = {
  step0: { clusters: [] },
  coverage: [
    { name: "Data pipelines", priority: "must_have", keywords: [], coverage_count: 2, matched_fact_ids: ["f1", "f2"] },
    { name: "Mentoring", priority: "preferred", keywords: [], coverage_count: 0, matched_fact_ids: [] },
  ],
  skills: [
    { skill: "Airflow", requested_as: "airflow", state: "verified" },
    { skill: "dbt", requested_as: "dbt", state: "supported" },
    { skill: "Spark", requested_as: "spark", state: "adjacent" },
    { skill: "Kafka", requested_as: "kafka", state: "unsupported" },
  ],
};
const FACTS = [
  { id: "f1", fact_type: "experience", value_json: { title: "Data Engineer", company: "Initech" } },
  { id: "f2", fact_type: "experience", value_json: { title: "Analyst", company: "Hooli" } },
];

function describeReads() {
  on("GET /resume-documents/mine?application_id=a1", DOCUMENT);
  on("POST /resume-documents/doc-1/coverage", COVERAGE);
  on("GET /profile/versions/9f8e7d6c5b4a3210/career-facts", FACTS);
  on("PATCH /resume-documents/doc-1/evidence", {});
}

function panel(options: { strict?: boolean } = {}) {
  const tree = (
    <MemoryRouter>
      <TailorPanel applicationId="a1" />
    </MemoryRouter>
  );
  return render(options.strict === true ? <StrictMode>{tree}</StrictMode> : tree);
}

const BUTTON = "Check coverage";
const SENTENCE =
  "Reads this posting's requirements with your model, then matches them to your profile by code. Pressing Check coverage makes a real model call on your own key. The match isn't saved, so each visit needs its own press.";

// The one thing that makes the model call: a press on the button.
async function press(container: HTMLElement) {
  click(buttonNamed(container, BUTTON));
  await settle();
}

const asked = () => calls.map((call) => `${call.method} ${call.path}`);

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("Tailor", () => {
  it("shows a placeholder in its own shape while its reads are out, and says on the button that it is working", async () => {
    on("GET /resume-documents/mine?application_id=a1", () => new Promise(() => undefined));
    const { container } = panel();
    await press(container);
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Checking coverage...");
    const button = buttonNamed(container, "Checking...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(text(container)).toContain(SENTENCE);
  });

  it("draws must-have in ivory bold and preferred muted, and each count in words", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    const priorities = [...container.querySelectorAll(".bj-ws-priority")];
    expect(priorities.map((node) => [text(node), node.classList.contains("bj-ws-priority--must")])).toEqual([["Must have", true], ["Preferred", false]]);
    expect([...container.querySelectorAll(".bj-ws-cluster-count")].map((node) => text(node))).toEqual(["2 matches", "0 matches"]);
  });

  it("says each skill in words as well as a mark: Verified ✓, Supported ◆, Adjacent ◇, Gap ○", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    expect([...container.querySelectorAll(".bj-ws-skill")].map((node) => text(node))).toEqual(["✓ Airflow Verified", "◆ dbt Supported", "◇ Spark Adjacent", "○ Kafka Gap"]);
    expect(container.querySelector(".bj-ws-skill--adjacent")).not.toBeNull();
  });

  it("opens a cluster to its evidence, as a disclosure with its state, and says picks do not change the draft yet", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    const row = buttonNamed(container, /Must have/);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect([...container.querySelectorAll(".bj-ws-evidence-item")].map((node) => text(node))).toEqual(["Data Engineer -- Initech", "Analyst -- Hooli"]);
    expect(text(container)).toContain("Picks save here. The draft doesn't read them yet.");
    click(row);
    expect(container.querySelector(".bj-ws-evidence")).toBeNull();
  });

  it("saves a pick through the evidence endpoint with the whole selection", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    click(buttonNamed(container, /Must have/));
    click(query(container, ".bj-ws-evidence-item input"));
    await settle();
    expect(callsTo("PATCH /resume-documents/doc-1/evidence")[0].body).toEqual({ evidence_fact_ids: ["f2"] });
  });

  it("keeps the panel when a save fails, says the picks were not saved, and retries the save without asking the model again", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    click(buttonNamed(container, /Must have/));
    on("PATCH /resume-documents/doc-1/evidence", new FakeApiError("The server hit a problem.", "INTERNAL"));
    click(query(container, ".bj-ws-evidence-item input"));
    await settle();
    expect(container.querySelectorAll(".bj-ws-cluster")).toHaveLength(2);
    expect(text(query(container, "[role='alert']"))).toContain("Couldn't save your picks.");
    on("PATCH /resume-documents/doc-1/evidence", {});
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(callsTo("PATCH /resume-documents/doc-1/evidence")).toHaveLength(2);
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(1);
    expect(container.querySelector("[role='alert']")).toBeNull();
  });

  it("names a failed read with a Retry that reads again, and asks the model nothing while the document read is failing", async () => {
    on("GET /resume-documents/mine?application_id=a1", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const { container } = panel();
    await press(container);
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    // Everything that was sent: the one read that failed, and nothing after it.
    expect(asked()).toEqual(["GET /resume-documents/mine?application_id=a1"]);
    describeReads();
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelectorAll(".bj-ws-cluster")).toHaveLength(2);
  });

  it("shows the notice with its link, and no button and no Retry, for a missing profile or key", async () => {
    on("GET /resume-documents/mine?application_id=a1", new FakeApiError("Add a model key.", "SETUP_REQUIRED", undefined, 409));
    const { container } = panel();
    await press(container);
    expect(text(query(container, ".bj-setup-notice"))).toContain("Add a model key.");
    expect(container.querySelector("button")).toBeNull();
    expect(text(container)).not.toContain(SENTENCE);
  });

  it("has a trail that says a model extracted the clusters and code matched them, with a receipt of what came back", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    const trigger = query(container, "button.bj-trail-trigger");
    expect(text(trigger)).toContain("clusters extracted by a model · matched to your profile by code");
    click(trigger);
    expect(text(query(container, ".bj-receipt"))).toContain("clusters2");
    expect(text(query(container, ".bj-receipt"))).toContain("profile9f8e7d6c");
  });

  it("passes an accessibility check", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    click(buttonNamed(container, /Must have/));
    await expectNoSeriousA11yViolations(container);
  });

  it("passes an accessibility check before it is pressed, and after a failed press", async () => {
    const { container } = panel();
    await expectNoSeriousA11yViolations(container);
    on("GET /resume-documents/mine?application_id=a1", new FakeApiError("The server hit a problem.", "INTERNAL"));
    await press(container);
    expect(container.querySelector("[role='alert']")).not.toBeNull();
    await expectNoSeriousA11yViolations(container);
  });
});

describe.each([
  ["plain", false],
  ["StrictMode", true],
])("Tailor on open and on remount (%s)", (_mode, strict) => {
  it("asks for nothing when it opens: one sentence about what a press does and costs, and the button", async () => {
    describeReads();
    const { container } = panel({ strict });
    await settle();
    expect(calls).toEqual([]);
    expect(text(query(container, ".bj-ws-intro"))).toBe(SENTENCE);
    expect(buttonNamed(container, BUTTON).getAttribute("aria-disabled")).toBeNull();
    expect(container.querySelector(".bj-ws-cluster")).toBeNull();
  });

  it("asks for nothing when it is opened again, and a second press is a second model call", async () => {
    describeReads();
    const first = panel({ strict });
    await press(first.container);
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(1);
    first.unmount();

    const before = calls.length;
    const second = panel({ strict });
    await settle();
    expect(calls).toHaveLength(before);
    expect(text(second.container)).toContain(SENTENCE);
    expect(second.container.querySelector(".bj-ws-cluster")).toBeNull();

    await press(second.container);
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(2);
    expect(second.container.querySelectorAll(".bj-ws-cluster")).toHaveLength(2);
  });
});

describe("Tailor when it is pressed", () => {
  it("reads the document, then the profile's facts, and asks the model last, once", async () => {
    describeReads();
    const { container } = panel();
    await press(container);
    expect(asked()).toEqual([
      "GET /resume-documents/mine?application_id=a1",
      "GET /profile/versions/9f8e7d6c5b4a3210/career-facts",
      "POST /resume-documents/doc-1/coverage",
    ]);
  });

  it("makes one model call for a double click", async () => {
    describeReads();
    const { container } = panel();
    const button = buttonNamed(container, BUTTON);
    click(button);
    click(button);
    await settle();
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(1);
    expect(callsTo("GET /resume-documents/mine?application_id=a1")).toHaveLength(1);
  });

  it.each([
    ["plain", false],
    ["StrictMode", true],
  ])("does not spend the model call when the person has left before the reads finished (%s)", async (_mode, strict) => {
    describeReads();
    let release: (value: unknown) => void = () => undefined;
    on("GET /resume-documents/mine?application_id=a1", () => new Promise((resolve) => (release = resolve)));
    const { container, unmount } = panel({ strict });
    click(buttonNamed(container, BUTTON));
    await settle();
    unmount();
    release(DOCUMENT);
    await settle();
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(0);
  });

  it("keeps the focus on a button that is working, and puts it on the result when the button goes away", async () => {
    describeReads();
    let release: (value: unknown) => void = () => undefined;
    on("POST /resume-documents/doc-1/coverage", () => new Promise((resolve) => (release = resolve)));
    const { container } = panel();
    const button = buttonNamed(container, BUTTON);
    button.focus();
    click(button);
    await settle();
    expect(buttonNamed(container, "Checking...")).toBe(button);
    expect(document.activeElement).toBe(button);

    release(COVERAGE);
    await settle();
    expect(container.contains(button)).toBe(false);
    const result = query(container, "[role='group'][aria-label='Coverage result']");
    expect(result.getAttribute("tabindex")).toBe("-1");
    expect(document.activeElement).toBe(result);
  });

  it("moves focus to the result without scrolling the page to it", async () => {
    describeReads();
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    const { container } = panel();
    await press(container);
    const result = query(container, "[role='group'][aria-label='Coverage result']");
    // jsdom does not scroll, so what the page would do is read from the options the call was made with.
    const onResult = focus.mock.calls.filter((_args, index) => focus.mock.contexts[index] === result);
    expect(onResult).toEqual([[{ preventScroll: true }]]);
    expect(document.activeElement).toBe(result);
  });

  it("leaves focus alone when the person has moved on to something else while it worked", async () => {
    describeReads();
    let release: (value: unknown) => void = () => undefined;
    on("POST /resume-documents/doc-1/coverage", () => new Promise((resolve) => (release = resolve)));
    const { container } = panel();
    const elsewhere = document.createElement("input");
    document.body.append(elsewhere);
    click(buttonNamed(container, BUTTON));
    await settle();
    elsewhere.focus();
    release(COVERAGE);
    await settle();
    expect(document.activeElement).toBe(elsewhere);
    elsewhere.remove();
  });

  it("does not spend the model call when the profile's facts cannot be read, and spends it once on Retry", async () => {
    describeReads();
    on("GET /profile/versions/9f8e7d6c5b4a3210/career-facts", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const { container } = panel();
    await press(container);
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(0);
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");

    on("GET /profile/versions/9f8e7d6c5b4a3210/career-facts", FACTS);
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(1);
    expect(container.querySelectorAll(".bj-ws-cluster")).toHaveLength(2);
  });

  it("keeps the sentence and the very same button when the model call fails, and a Retry hands focus back to that button", async () => {
    describeReads();
    on("POST /resume-documents/doc-1/coverage", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const { container } = panel();
    const button = buttonNamed(container, BUTTON);
    await press(container);
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    expect(text(container)).toContain(SENTENCE);
    expect(buttonNamed(container, BUTTON)).toBe(button);

    // The second try stays out, so the panel is still working when focus is read.
    on("POST /resume-documents/doc-1/coverage", () => new Promise(() => undefined));
    const retry = buttonNamed(container, "Retry");
    retry.focus();
    click(retry);
    await settle();
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(2);
    expect(document.activeElement).toBe(button);
  });

  it("makes one model call when the button is pressed again from the failed state", async () => {
    describeReads();
    on("GET /profile/versions/9f8e7d6c5b4a3210/career-facts", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const { container } = panel();
    await press(container);
    expect(container.querySelector("[role='alert']")).not.toBeNull();
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(0);

    on("GET /profile/versions/9f8e7d6c5b4a3210/career-facts", FACTS);
    await press(container);
    expect(callsTo("POST /resume-documents/doc-1/coverage")).toHaveLength(1);
    expect(container.querySelectorAll(".bj-ws-cluster")).toHaveLength(2);
    expect(container.querySelector("[role='alert']")).toBeNull();
  });

  it("starts from the picks already saved for the resume, and from every match only when none were saved, without saving either", async () => {
    on("GET /resume-documents/mine?application_id=a1", { ...DOCUMENT, selected_evidence_fact_ids: ["f2"] });
    on("POST /resume-documents/doc-1/coverage", COVERAGE);
    on("GET /profile/versions/9f8e7d6c5b4a3210/career-facts", FACTS);
    const saved = panel();
    await press(saved.container);
    click(buttonNamed(saved.container, /Must have/));
    expect([...saved.container.querySelectorAll<HTMLInputElement>(".bj-ws-evidence-item input")].map((box) => box.checked)).toEqual([false, true]);
    expect(callsTo("PATCH /resume-documents/doc-1/evidence")).toHaveLength(0);
    saved.unmount();

    on("GET /resume-documents/mine?application_id=a1", { ...DOCUMENT, selected_evidence_fact_ids: [] });
    const none = panel();
    await press(none.container);
    click(buttonNamed(none.container, /Must have/));
    expect([...none.container.querySelectorAll<HTMLInputElement>(".bj-ws-evidence-item input")].map((box) => box.checked)).toEqual([true, true]);
    expect(callsTo("PATCH /resume-documents/doc-1/evidence")).toHaveLength(0);
  });

  it("shows only the notice when the model call is refused for setup: no button, no Retry, no sentence", async () => {
    describeReads();
    on("POST /resume-documents/doc-1/coverage", new FakeApiError("Add a model key.", "SETUP_REQUIRED", undefined, 409));
    const { container } = panel();
    await press(container);
    expect(text(query(container, ".bj-setup-notice"))).toContain("Add a model key.");
    expect(container.querySelector("button")).toBeNull();
    expect(text(container)).not.toContain(SENTENCE);
  });

  it("says how long to wait when the platform's limit is reached, and keeps the button and its Retry", async () => {
    describeReads();
    on("POST /resume-documents/doc-1/coverage", Object.assign(new FakeApiError("Slow down.", "RATE_LIMITED", undefined, 429), { retryAfterSeconds: 700 }));
    const { container } = panel();
    await press(container);
    expect(text(query(container, "[role='alert']"))).toContain("You are doing that too often. Try again in about 12 minutes.");
    expect(buttonNamed(container, BUTTON)).toBeTruthy();
    expect(buttonNamed(container, "Retry")).toBeTruthy();
  });
});
