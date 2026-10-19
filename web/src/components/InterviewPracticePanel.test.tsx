// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text, typeInto } from "../testing/forms";
import { NOW, SESSION, FakeSetupError, answered, question } from "../testing/researchFixtures";
import { buttonNamed, click, query, render } from "../testing/render";
import { InterviewPracticePanel } from "./InterviewPracticePanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const BASE = "/applications/a1/interview-practice/sessions";
const DETAIL = `${BASE}/${SESSION.id}`;

function panel() {
  return render(
    <MemoryRouter>
      <InterviewPracticePanel applicationId="a1" now={NOW} />
    </MemoryRouter>,
  );
}

const five = (answeredCount: number, overrides: Record<string, unknown> = {}) =>
  Array.from({ length: 5 }, (_, index) => (index < answeredCount ? answered(index + 1, 6 + (index % 3), overrides) : question(index + 1)));

function resume(questions: unknown[], session: Record<string, unknown> = {}) {
  on(`GET ${BASE}`, { sessions: [{ ...SESSION, ...session }] });
  on(`GET ${DETAIL}`, { session: { ...SESSION, ...session }, questions });
}

function answerFeedback(score: number) {
  return { score, structure_feedback: "A clear opening.", specificity_feedback: "Name the tool you used.", star_coverage: { situation: true, task: true, action: true, result: false }, improved_answer: "A stronger take." };
}

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("Practice: loading, failure and empty", () => {
  it("shows a placeholder while the sessions are read", () => {
    on(`GET ${BASE}`, () => new Promise(() => undefined));
    const { container } = panel();
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Loading practice sessions...");
    expect(container.querySelector(".bj-trail")).toBeNull();
  });

  it("names a failed read with a Retry that reads again, and marks the block's trail", async () => {
    on(`GET ${BASE}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    expect(text(query(container, ".bj-trail"))).toBe("✕ Practice couldn't be read");
    resume(five(0));
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(text(container)).toContain("Question 1 of 5");
  });

  it("teaches what a session is and what it needs, and draws no example question and no trail", async () => {
    on(`GET ${BASE}`, { sessions: [] });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-empty h3"))).toBe("No practice session yet");
    expect(text(query(container, ".bj-rs-empty"))).toContain("five questions");
    expect(text(query(container, ".bj-rs-empty"))).toContain("It needs a model key and an active profile.");
    expect(container.querySelector(".bj-rs-question, .bj-trail")).toBeNull();
    expect(buttonNamed(container, "Start practice session")).toBeTruthy();
  });
});

describe("Practice: starting", () => {
  it("starts a session, lands keyboard focus on the answer box and draws the session's trail from its own fields", async () => {
    on(`GET ${BASE}`, { sessions: [] });
    on(`POST ${BASE}`, { session: SESSION, questions: five(0) });
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Start practice session"));
    await settle();
    expect(text(query(container, ".bj-rs-question .bj-rs-eyebrow"))).toBe("Question 1 of 5 · Behavioral · Targets Mentoring");
    expect(document.activeElement).toBe(query(container, "textarea"));
    expect(text(query(container, ".bj-trail"))).toBe("✦ Questions asked by your model · from your resume evidence · Sep 21");
  });

  it("keeps the panel and says what failed, with a Retry that starts again", async () => {
    on(`GET ${BASE}`, { sessions: [] });
    on(`POST ${BASE}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Start practice session"));
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    expect(container.querySelector(".bj-rs-empty")).not.toBeNull();
    on(`POST ${BASE}`, { session: SESSION, questions: five(0) });
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(callsTo(`POST ${BASE}`)).toHaveLength(2);
  });

  it("links to what the server says is missing, with no Retry", async () => {
    on(`GET ${BASE}`, { sessions: [] });
    on(`POST ${BASE}`, new FakeSetupError("Interview practice needs a model key.", "/profile/integrations", "interview_practice", ["credential"]));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Start practice session"));
    await settle();
    const link = query<HTMLAnchorElement>(container, ".bj-setup-notice a");
    expect(link.getAttribute("href")).toBe("/settings");
    expect(text(link)).toBe("Add a model key in Settings");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
  });

  it("sends one request for two presses and busies only its button", async () => {
    on(`GET ${BASE}`, { sessions: [] });
    let finish: (value: unknown) => void = () => undefined;
    on(`POST ${BASE}`, () => new Promise((resolve) => (finish = resolve)));
    const { container } = panel();
    await settle();
    const button = buttonNamed(container, "Start practice session");
    click(button);
    await settle(1);
    expect(button.textContent).toBe("Starting...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    click(button);
    expect(callsTo(`POST ${BASE}`)).toHaveLength(1);
    finish({ session: SESSION, questions: five(0) });
    await settle();
  });
});

describe("Practice: a session read back from an earlier visit", () => {
  it("lands on the first unanswered question with the answers already given above it, each with its score", async () => {
    resume(five(2));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-question .bj-rs-eyebrow"))).toBe("Question 3 of 5 · Behavioral · Targets Mentoring");
    const rows = [...container.querySelectorAll("button.bj-rs-answer-row")].map((button) => [text(button.querySelector(".bj-rs-answer-name")!), text(button.querySelector(".bj-rs-answer-score")!)]);
    expect(rows).toEqual([
      ["Question 1 · Behavioral", "6 / 10"],
      ["Question 2 · Technical", "7 / 10"],
    ]);
    // The answer box is not stolen from the page on arrival.
    expect(document.activeElement).not.toBe(query(container, "textarea"));
  });

  it("opens an earlier answer to its question, what was said, a score, the comments, STAR as gold segments and a stronger answer", async () => {
    resume(five(2));
    const { container } = panel();
    await settle();
    const row = buttonNamed(container, /Question 1/);
    expect(row.getAttribute("aria-expanded")).toBe("false");
    click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    const review = query(container, ".bj-rs-review");
    expect(text(review.querySelector(".bj-rs-text")!)).toBe("Question text 1?");
    expect(text(review)).toContain("My answer to 1.");
    const meter = query(review, "[role='meter']");
    expect(meter.getAttribute("aria-valuetext")).toBe("6 / 10");
    expect(meter.querySelectorAll(".bj-segbar-cell--filled")).toHaveLength(6);
    expect(text(review)).toContain("Structure comment 1.");
    expect(text(review)).toContain("A stronger answer 1.");
    // STAR: gold where the model found the part, an outline where it did not, with the word either way.
    const parts = [...review.querySelectorAll(".bj-rs-star-part")].map((part) => [text(part), part.classList.contains("bj-rs-star-part--on")]);
    expect(parts).toEqual([
      ["Situation: found", true],
      ["Task: found", true],
      ["Action: not found", false],
      ["Result: not found", false],
    ]);
    expect(text(review)).toContain("✦ Judged by your model");
    click(row);
    expect(container.querySelector(".bj-rs-review")).toBeNull();
  });

  it("says STAR is not scored for a technical answer instead of drawing empty parts", async () => {
    resume(five(2));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, /Question 2/));
    expect(container.querySelector(".bj-rs-star")).toBeNull();
    expect(text(query(container, ".bj-rs-review"))).toContain("STAR structure is not scored for technical questions.");
  });

  it("gives each earlier answer its own trail with the time the server stored it", async () => {
    resume(five(1));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, /Question 1/));
    expect(text(query(container, ".bj-rs-review .bj-trail"))).toBe("✦ Scored by your model · on your model key · Sep 21");
  });

  it("says a row with no stored score or feedback has none, rather than inventing one", async () => {
    resume([answered(1, 7, { score: null, feedback: null }), ...[2, 3, 4, 5].map((n) => question(n))]);
    const { container } = panel();
    await settle();
    expect(text(query(container, "button.bj-rs-answer-row"))).toContain("No score stored");
    click(query(container, "button.bj-rs-answer-row"));
    expect(text(query(container, ".bj-rs-review"))).toContain("No score or feedback was stored for this answer.");
    expect(container.querySelector("[role='meter']")).toBeNull();
  });

  it("shows a completed session as its report with the day it was completed, and offers a new one", async () => {
    resume(five(5), { status: "completed", completed_at: "2026-09-21T12:30:00Z" });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-report h3"))).toBe("Session complete");
    expect(text(query(container, ".bj-rs-report .bj-rs-meta"))).toContain("5 of 5 answered · average score 6.8 / 10");
    expect([...container.querySelectorAll(".bj-rs-meta")].some((node) => text(node) === "Session completed Sep 21")).toBe(true);
    expect(container.querySelector("textarea")).toBeNull();
    expect(buttonNamed(container, "Practice again")).toBeTruthy();
  });

  it("says that grounding research is shared across users, in the question and in the receipt", async () => {
    resume([question(1, { grounded_in: "Two rounds on data modeling" }), ...[2, 3, 4, 5].map((n) => question(n))], { registry_entry_id: "r1" });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-question .bj-ws-caption"))).toBe('Grounded in interview-process research for this company, which is shared across users: "Two rounds on data modeling"');
    click(query(container, "button.bj-trail-trigger"));
    expect(text(query(container, ".bj-receipt"))).toContain("shared across users");
    expect(text(query(container, ".bj-receipt"))).toContain("another person's model key");
  });

  it("says a general question has no company-specific data behind it", async () => {
    resume(five(0));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-question .bj-ws-caption"))).toBe("General question -- no cited company-specific data behind this one.");
  });
});

describe("Practice: answering", () => {
  const ANSWERS = `${DETAIL}/answers`;

  it("does not send an empty answer: the button is aria-disabled and says what is missing", async () => {
    resume(five(0));
    const { container } = panel();
    await settle();
    const submit = buttonNamed(container, "Submit answer");
    expect(submit.getAttribute("aria-disabled")).toBe("true");
    expect(submit.hasAttribute("disabled")).toBe(false);
    expect(text(container)).toContain("Write an answer to submit it.");
    click(submit);
    expect(callsTo(`POST ${ANSWERS}`)).toHaveLength(0);
  });

  it("sends the answer, busies only the button, then shows the feedback for it and moves focus there", async () => {
    resume(five(0));
    let finish: (value: unknown) => void = () => undefined;
    on(`POST ${ANSWERS}`, () => new Promise((resolve) => (finish = resolve)));
    const { container } = panel();
    await settle();
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "I led the migration.");
    const submit = buttonNamed(container, "Submit answer");
    click(submit);
    await settle(1);
    expect(callsTo(`POST ${ANSWERS}`)[0].body).toEqual({ answer_text: "I led the migration." });
    expect(submit.textContent).toBe("Scoring...");
    expect(submit.getAttribute("aria-disabled")).toBe("true");
    click(submit);
    expect(callsTo(`POST ${ANSWERS}`)).toHaveLength(1);
    finish({ feedback: answerFeedback(7), next_question: question(2), session_report: null });
    await settle();
    expect(document.activeElement).toBe(query(container, ".bj-rs-feedback h3"));
    expect(text(query(container, ".bj-rs-feedback"))).toContain("I led the migration.");
    expect(query(container, ".bj-rs-feedback [role='meter']").getAttribute("aria-valuetext")).toBe("7 / 10");
  });

  it("gives a just-scored answer a trail with no time (none came back with it), then moves to the next question with focus on its box", async () => {
    resume(five(0));
    on(`POST ${ANSWERS}`, { feedback: answerFeedback(7), next_question: question(2), session_report: null });
    const { container } = panel();
    await settle();
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "I led the migration.");
    click(buttonNamed(container, "Submit answer"));
    await settle();
    expect(text(query(container, ".bj-rs-feedback .bj-trail"))).toBe("✦ Scored by your model · on your model key");
    click(buttonNamed(container, "Next question"));
    await settle(1);
    expect(text(query(container, ".bj-rs-question .bj-rs-eyebrow"))).toContain("Question 2 of 5");
    expect(document.activeElement).toBe(query(container, "textarea"));
    expect(query<HTMLTextAreaElement>(container, "textarea").value).toBe("");
    // The answer just given is now an earlier one, and still says nothing of when.
    click(buttonNamed(container, /Question 1/));
    expect(text(query(container, ".bj-rs-review .bj-trail"))).toBe("✦ Scored by your model · on your model key");
  });

  it("does not call the session complete after an answer that leaves questions to come, and offers no Practice again", async () => {
    resume(five(0));
    on(`POST ${ANSWERS}`, { feedback: answerFeedback(7), next_question: question(2), session_report: null });
    const { container } = panel();
    await settle();
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "I led the migration.");
    click(buttonNamed(container, "Submit answer"));
    await settle();
    expect(container.querySelector(".bj-rs-feedback")).not.toBeNull();
    expect(container.querySelector(".bj-rs-report")).toBeNull();
    expect(text(container)).not.toContain("Session complete");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Practice again")).toBe(false);
    expect(buttonNamed(container, "Next question")).toBeTruthy();
    expect(callsTo(`POST ${BASE}`)).toHaveLength(0);
  });

  it("keeps the draft when scoring fails, says so, and Send again sends the same answer", async () => {
    resume(five(0));
    on(`POST ${ANSWERS}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "My draft answer.");
    click(buttonNamed(container, "Submit answer"));
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    expect(query<HTMLTextAreaElement>(container, "textarea").value).toBe("My draft answer.");
    on(`POST ${ANSWERS}`, { feedback: answerFeedback(5), next_question: question(2), session_report: null });
    click(buttonNamed(container, "Send again"));
    await settle();
    expect(callsTo(`POST ${ANSWERS}`).map((call) => call.body)).toEqual([{ answer_text: "My draft answer." }, { answer_text: "My draft answer." }]);
    expect(container.querySelector(".bj-rs-feedback")).not.toBeNull();
  });

  it("links to what is missing when scoring needs a key the person removed, keeps the draft and offers no Send again", async () => {
    resume(five(0));
    on(`POST ${ANSWERS}`, new FakeSetupError("Interview practice needs a model key.", "/profile/integrations", "interview_practice", ["credential"]));
    const { container } = panel();
    await settle();
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "My draft answer.");
    click(buttonNamed(container, "Submit answer"));
    await settle();
    expect(query<HTMLAnchorElement>(container, ".bj-setup-notice a").getAttribute("href")).toBe("/settings");
    expect(query<HTMLTextAreaElement>(container, "textarea").value).toBe("My draft answer.");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Send again")).toBe(false);
  });

  it("finishes the session on the last answer: the feedback, then the report with no completion time, and a new session on request", async () => {
    resume(five(4));
    on(`POST ${ANSWERS}`, { feedback: answerFeedback(8), next_question: null, session_report: { question_count: 5, answered_count: 5, average_score: 6.8, star_coverage_rate: 0.5 } });
    const { container } = panel();
    await settle();
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "Final answer.");
    click(buttonNamed(container, "Submit answer"));
    await settle();
    expect(container.querySelector(".bj-rs-feedback")).not.toBeNull();
    expect(container.querySelector("button")).not.toBeNull();
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Next question")).toBe(false);
    expect(text(query(container, ".bj-rs-report .bj-rs-meta"))).toContain("5 of 5 answered · average score 6.8 / 10 · full STAR coverage on 50% of behavioral and situational answers");
    expect([...container.querySelectorAll(".bj-rs-meta")].some((node) => text(node).startsWith("Session completed"))).toBe(false);
    on(`POST ${BASE}`, { session: { ...SESSION, id: "s2" }, questions: five(0) });
    click(buttonNamed(container, "Practice again"));
    await settle();
    expect(text(container)).toContain("Question 1 of 5");
  });
});

describe("Practice: the page's one cyan fill is the band's, and the markup is sound", () => {
  it("draws no primary button in any state", async () => {
    resume(five(2));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, /Question 1/));
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });

  it.each([
    ["empty", () => on(`GET ${BASE}`, { sessions: [] })],
    ["an in-progress session with an open answer", () => resume(five(2))],
    ["a completed session", () => resume(five(5), { status: "completed", completed_at: "2026-09-21T12:30:00Z" })],
  ])("has no serious accessibility violation (%s)", async (_name, describeReads) => {
    describeReads();
    const { container } = panel();
    await settle();
    const row = container.querySelector<HTMLButtonElement>("button.bj-rs-answer-row");
    if (row !== null) click(row);
    await expectNoSeriousA11yViolations(container);
  });
});
