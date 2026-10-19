// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AtsAttempt } from "../lib/generateTypes";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text, typeInto } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { GapInterview } from "./GapInterview";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const BREAKDOWN = { semantic_coverage: 20, experience_quality: 20, hard_req_score: 8, quantification: 6, company_alignment: 4, structure: 5 };
const attempt = (score: number): AtsAttempt => ({ overall_score: score, breakdown: BREAKDOWN, confidence: "medium", rating: "Moderate", gaps: [] });
const QUESTIONS = { questions: [{ cluster_name: "Streaming systems", question: "Have you worked with an event stream?" }, { cluster_name: "Mentoring", question: "Did you mentor anyone?" }] };

beforeEach(() => {
  reset();
  on("GET /resume-documents/mine?application_id=a1", { id: "doc-1" });
});
afterEach(() => vi.restoreAllMocks());

function open() {
  const view = render(<GapInterview applicationId="a1" attempts={[attempt(61)]} onRegenerate={() => undefined} />);
  return view;
}

describe("Close the gaps", () => {
  it("renders only when the latest score is under 70", () => {
    expect(render(<GapInterview applicationId="a1" attempts={[attempt(70)]} onRegenerate={() => undefined} />).container.innerHTML).toBe("");
    expect(render(<GapInterview applicationId="a1" attempts={[]} onRegenerate={() => undefined} />).container.innerHTML).toBe("");
    expect(buttonNamed(open().container, "Close the gaps")).toBeTruthy();
  });

  it("asks for questions only on the click, says how many and that a no costs nothing", async () => {
    on("POST /resume-documents/doc-1/gap-interview", QUESTIONS);
    const { container } = open();
    await settle();
    expect(callsTo("POST /resume-documents/doc-1/gap-interview")).toHaveLength(0);
    expect(text(container)).toContain("Up to 3 questions");
    click(buttonNamed(container, "Close the gaps"));
    await settle();
    expect(text(container)).toContain("Have you worked with an event stream?");
    expect(text(container)).toContain("Streaming systems");
    expect(text(query(container, ".bj-ws-gap-head"))).toContain("Worded by your model");
    expect(text(container)).toContain("Hard cap: 3 questions per application.");
  });

  it("says when there is no honest gap to ask about, and names a failure with Try again", async () => {
    on("POST /resume-documents/doc-1/gap-interview", { questions: [] });
    const empty = open();
    click(buttonNamed(empty.container, "Close the gaps"));
    await settle();
    expect(text(empty.container)).toContain("No honest gap to ask about right now");
    empty.unmount();
    on("POST /resume-documents/doc-1/gap-interview", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const failed = open();
    click(buttonNamed(failed.container, "Close the gaps"));
    await settle();
    expect(text(query(failed.container, "[role='alert']"))).toContain("The server hit a problem.");
    on("POST /resume-documents/doc-1/gap-interview", QUESTIONS);
    click(buttonNamed(failed.container, "Try again"));
    await settle();
    expect(text(failed.container)).toContain("Have you worked with an event stream?");
  });

  it("goes from a yes to a drafted fact the person approves, saves it as a pending version and offers to activate it, every button outlined", async () => {
    on("POST /resume-documents/doc-1/gap-interview", QUESTIONS);
    on("POST /profile/gap-interview/draft", { bullet: "Built an event stream consumer.", entity_pointer: "/experience/0", candidates: [{ pointer: "/experience/0", label: "Data Engineer -- Initech" }] });
    on("POST /profile/gap-interview/approve", { id: "pv-9" });
    on("POST /profile/versions/pv-9/activate", {});
    const regenerate = vi.fn();
    const { container } = render(<GapInterview applicationId="a1" attempts={[attempt(61)]} onRegenerate={regenerate} />);
    click(buttonNamed(container, "Close the gaps"));
    await settle();
    click(buttonNamed(container, "Yes, add detail"));
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "I wrote a Kafka consumer.");
    click(buttonNamed(container, /Draft the fact/));
    await settle();
    expect(callsTo("POST /profile/gap-interview/draft")[0].body).toEqual({ question: "Have you worked with an event stream?", answer: "I wrote a Kafka consumer." });
    expect(text(container)).toContain("◆ New fact, not saved yet");
    expect(text(container)).toContain("Data Engineer -- Initech");
    click(buttonNamed(container, /Approve & save to profile/));
    await settle();
    expect(callsTo("POST /profile/gap-interview/approve")[0].body).toEqual({ bullet: "Built an event stream consumer.", entity_pointer: "/experience/0" });
    expect(text(container)).toContain("Saved as a new profile version, pending");
    click(buttonNamed(container, "Activate now"));
    await settle();
    click(buttonNamed(container, "Generate again"));
    expect(regenerate).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".bj-primary")).toBeNull();
  });

  // Every step replaces the button that was pressed, which drops keyboard focus to the top of the page: it goes
  // to the block, which stays mounted, and a person who has moved on is left alone.
  it("keeps keyboard focus in the block through every step", async () => {
    on("POST /resume-documents/doc-1/gap-interview", QUESTIONS);
    on("POST /profile/gap-interview/draft", { bullet: "Built an event stream consumer.", entity_pointer: "/experience/0", candidates: [{ pointer: "/experience/0", label: "Data Engineer -- Initech" }] });
    on("POST /profile/gap-interview/approve", { id: "pv-9" });
    on("POST /profile/versions/pv-9/activate", {});
    const { container } = open();
    const block = query(container, ".bj-ws-gap");
    const press = async (name: string | RegExp) => {
      const button = buttonNamed(container, name);
      button.focus();
      click(button);
      await settle();
    };
    await press("Close the gaps");
    expect(document.activeElement).toBe(block);
    await press("Yes, add detail");
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "I wrote a Kafka consumer.");
    await press(/Draft the fact/);
    expect(block.contains(document.activeElement)).toBe(true);
    await press("Approve & save to profile");
    expect(block.contains(document.activeElement)).toBe(true);
    await press("Activate now");
    expect(block.contains(document.activeElement)).toBe(true);
    expect(text(container)).toContain("✓ Active");
  });

  it("shows no estimated score change anywhere", async () => {
    on("POST /resume-documents/doc-1/gap-interview", QUESTIONS);
    const { container } = open();
    click(buttonNamed(container, "Close the gaps"));
    await settle();
    expect(text(container)).not.toMatch(/\d+ ?(->|→) ?~?\d+/);
  });

  it("passes an accessibility check with a question open", async () => {
    on("POST /resume-documents/doc-1/gap-interview", QUESTIONS);
    const { container } = open();
    click(buttonNamed(container, "Close the gaps"));
    await settle();
    await expectNoSeriousA11yViolations(container);
  });
});
