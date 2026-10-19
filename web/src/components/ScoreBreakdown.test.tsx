// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AtsAttempt } from "../lib/generateTypes";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { ScoreBreakdown } from "./ScoreBreakdown";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

// The ATS score block as the workspace draws it. Synthetic numbers that add up: 27 + 22 + 8 + 6 + 4 + 5 = 72.

const BREAKDOWN = { semantic_coverage: 27, experience_quality: 22, hard_req_score: 8, quantification: 6, company_alignment: 4, structure: 5 };

function attempt(overrides: Partial<AtsAttempt> = {}, score = 72): AtsAttempt {
  return { overall_score: score, breakdown: BREAKDOWN, confidence: "medium", rating: "Moderate", gaps: [], ...overrides };
}

const DETAIL = {
  clusters: [{ cluster_name: "Data pipelines", coverage_score: 80, years_ratio: 0.5, points_earned: 12, points_possible: 15 }],
  experience: { relevant_years: 4.5, total_years: 7, relevance_ratio: 0.64, has_management: false, management_bonus: 5 },
  quantification: { metrics_count: 6, relevance_score: 70 },
  company_alignment: { culture_score: 72, has_white_text_spam: false },
};

const values = (root: ParentNode) => [...root.querySelectorAll(".bj-segbar-value")].map((node) => text(node));
const labels = (root: ParentNode) => [...root.querySelectorAll(".bj-segbar-label")].map((node) => text(node));

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("the score block", () => {
  it("draws the numeral over five segmented bars, never a gauge, and the parts add up to the total", () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt()]} />);
    expect(text(query(container, ".bj-scoreblock-numeral"))).toBe("72");
    expect(text(query(container, ".bj-scoreblock-of"))).toContain("/ 100 · medium confidence");
    expect(labels(container)).toEqual(["Semantic coverage", "Experience quality", "Hard requirements", "Quantified impact", "Company alignment · structure"]);
    expect(values(container)).toEqual(["27 / 35 · near max", "22 / 30 · near max", "8 / 15 · has headroom", "6 / 10 · near max", "9 / 10 · near max"]);
    const parts = values(container).map((words) => Number(words.split(" / ")[0]));
    expect(parts.reduce((sum, part) => sum + part, 0)).toBe(72);
    expect(container.querySelectorAll("[role='meter']")).toHaveLength(5);
    expect(container.innerHTML).not.toMatch(/<svg|<canvas|radial|conic/);
    expect(text(container)).toContain("An honest number, not a verdict");
    expect(text(container)).toContain("engine");
  });

  it("draws one cell per point: 35, 30, 15, 10 and 10", () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt()]} />);
    const cells = [...container.querySelectorAll(".bj-segbar-cells")].map((row) => row.children.length);
    expect(cells).toEqual([35, 30, 15, 10, 10]);
    const filled = [...container.querySelectorAll(".bj-segbar-cells")].map((row) => row.querySelectorAll(".bj-segbar-cell--filled").length);
    expect(filled).toEqual([27, 22, 8, 6, 9]);
  });

  it("says a regeneration as first -> final, in words for a screen reader", () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({}, 64), attempt()]} />);
    expect(text(query(container, ".bj-scoreblock-numeral"))).toContain("64 -> 72");
    expect(text(query(container, ".bj-visually-hidden"))).toBe("was 64, now 72");
  });

  it("draws nothing for an engine that did not score", () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("draws the management credit as exact hatched headroom, never solid, and marks a caption only where it says 'judged'", () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ detail: DETAIL })]} />);
    const experience = container.querySelectorAll(".bj-segbar-cells")[1];
    expect(experience.querySelectorAll(".bj-segbar-cell--hatched").length).toBeGreaterThan(0);
    expect(experience.querySelectorAll(".bj-segbar-cell--filled")).toHaveLength(22);
    const details = [...container.querySelectorAll(".bj-segbar-detail")];
    expect(details.map((node) => text(node.querySelector(".bj-trail-glyph") ?? document.body) === "✦")).toEqual([true, true, false]);
    expect(text(details[2])).toBe("Culture-fit signal 72%");
  });

  it("explains a binding hard-requirements cap in a neutral note, and a real penalty with a red cross and its words", () => {
    const capped = { ...BREAKDOWN, hard_req_score: 0, semantic_coverage: 35, experience_quality: 30 };
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ breakdown: capped, detail: { ...DETAIL, company_alignment: { culture_score: 10, has_white_text_spam: true } } }, 65)]} />);
    expect(text(container)).toContain("cap the total at 65");
    const penalty = [...container.querySelectorAll(".bj-ws-callout")].find((node) => text(node).includes("8-point penalty"))!;
    expect(penalty.querySelector(".bj-trail-glyph--danger")?.textContent).toBe("✕");
    expect(container.querySelector(".bj-ws-callout--danger")).toBeNull();
  });

  it("opens the skill-cluster arithmetic on demand", () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ detail: DETAIL })]} />);
    expect(container.querySelector(".bj-ws-clusterlist")).toBeNull();
    const toggle = buttonNamed(container, /skill-cluster breakdown/);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(text(query(container, ".bj-ws-clusterlist"))).toContain("80% coverage · 12 / 15 pts · 50% of years matched");
  });
});

describe("dealbreakers", () => {
  const gaps = ["Missing dealbreaker: Active security clearance", "Weak/missing requirement: Kafka"];

  it("lists a missing dealbreaker as ✕ Missing with the exact gain, and a weak requirement as a tagged line", () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ gaps })]} />);
    const row = query(container, ".bj-ws-dealbreaker");
    expect(text(row)).toContain("✕ Missing");
    expect(text(row)).toContain("Active security clearance");
    expect(text(row)).toContain("is worth +7 exact");
    expect(text(query(container, ".bj-ws-tagged-list"))).toBe("Weak coverage Kafka");
  });

  it("saves 'This works for me', shows ✓ Asserted, and an adjusted total that says it is exact and unconfirmed", async () => {
    on("GET /resume-documents/mine?application_id=a1", { id: "doc-1", assertions: ["Already there"] });
    on("PATCH /resume-documents/doc-1/assertions", {});
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ gaps })]} />);
    click(buttonNamed(container, "This works for me"));
    await settle();
    expect(callsTo("PATCH /resume-documents/doc-1/assertions")[0].body).toEqual({ assertions: ["Already there", "Active security clearance"] });
    expect(text(query(container, ".bj-ws-dealbreaker"))).toContain("✓ Asserted");
    expect(text(query(container, ".bj-ws-adjusted"))).toBe("Adjusted: 72 -> 79 · exact -- confirmed once you generate again.");
    // The stored score is untouched.
    expect(text(query(container, ".bj-scoreblock-numeral"))).toBe("72");
  });

  // The button is replaced by the line that says it was saved, and a removed control drops keyboard focus to the page.
  it("moves focus to the 'Saved' line that replaces the button the keyboard pressed, and leaves a person who has moved on alone", async () => {
    on("GET /resume-documents/mine?application_id=a1", { id: "doc-1", assertions: [] });
    on("PATCH /resume-documents/doc-1/assertions", {});
    const { container } = render(
      <div>
        <ScoreBreakdown applicationId="a1" attempts={[attempt({ gaps })]} />
        <button type="button" id="elsewhere">
          Elsewhere
        </button>
      </div>,
    );
    const button = buttonNamed(container, "This works for me");
    button.focus();
    click(button);
    await settle();
    const saved = query(container, ".bj-ws-dealbreaker .bj-ws-caption");
    expect(text(saved)).toContain("Saved --");
    expect(document.activeElement).toBe(saved);

    const other = render(
      <div>
        <ScoreBreakdown applicationId="a1" attempts={[attempt({ gaps })]} />
        <button type="button" id="elsewhere-2">
          Elsewhere
        </button>
      </div>,
    );
    click(buttonNamed(other.container, "This works for me"));
    query<HTMLButtonElement>(other.container, "#elsewhere-2").focus();
    await settle();
    expect(document.activeElement).toBe(query(other.container, "#elsewhere-2"));
  });

  it("shows a failed save on the row and keeps it missing", async () => {
    on("GET /resume-documents/mine?application_id=a1", { id: "doc-1" });
    on("PATCH /resume-documents/doc-1/assertions", new FakeApiError("The server hit a problem.", "INTERNAL"));
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ gaps })]} />);
    click(buttonNamed(container, "This works for me"));
    await settle();
    expect(text(query(container, ".bj-ws-dealbreaker"))).toContain("The server hit a problem.");
    expect(text(query(container, ".bj-ws-dealbreaker"))).toContain("✕ Missing");
  });

  it("holds still while saving: aria-disabled and a second press does nothing", async () => {
    on("GET /resume-documents/mine?application_id=a1", () => new Promise(() => undefined));
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ gaps })]} />);
    const button = buttonNamed(container, "This works for me");
    click(button);
    await settle(1);
    expect(buttonNamed(container, "Saving...").getAttribute("aria-disabled")).toBe("true");
    click(buttonNamed(container, "Saving..."));
    expect(callsTo("GET /resume-documents/mine?application_id=a1")).toHaveLength(1);
  });

  it("passes an accessibility check with every part showing", async () => {
    const { container } = render(<ScoreBreakdown applicationId="a1" attempts={[attempt({ gaps, detail: DETAIL }, 72)]} />);
    click(buttonNamed(container, /skill-cluster breakdown/));
    await expectNoSeriousA11yViolations(container);
  });
});
