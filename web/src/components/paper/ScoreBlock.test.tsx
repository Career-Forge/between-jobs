// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { query, render } from "../../testing/render";
import { FitLadder, ScoreBlock } from "./ScoreBlock";
import { SegmentedBar } from "./SegmentedBar";

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const LEVELS = ["Poor", "Mixed", "Good", "Strong"];
const filledCells = (root: ParentNode) => root.querySelectorAll(".bj-ladder-cell--filled").length;

describe("ScoreBlock", () => {
  it("is the label, the engine mark, the numeral with its out-of and caption, the body and the footer", () => {
    const { container } = render(
      <ScoreBlock label="ATS score" value={72} caption="medium confidence" footer="An honest number, not a verdict.">
        <SegmentedBar label="Semantic coverage" value={27} max={35} />
      </ScoreBlock>,
    );
    expect(text(query(container, ".bj-scoreblock-label"))).toBe("ATS score");
    expect(text(query(container, ".bj-scoreblock-engine"))).toBe("engine ✦");
    expect(query(container, ".bj-scoreblock-engine [aria-hidden]").textContent).toBe("✦");
    expect(text(query(container, ".bj-scoreblock-numeral-row"))).toBe("72 / 100 · medium confidence");
    expect(query(container, ".bj-scoreblock-body [role='meter']")).not.toBeNull();
    expect(text(query(container, ".bj-scoreblock-footer"))).toBe("An honest number, not a verdict.");
  });

  it("is never a gauge, ring, radar or bare number: no svg, canvas or progress element", () => {
    const { container } = render(<ScoreBlock label="ATS score" value={72} />);
    expect(container.querySelector("svg, canvas, progress, meter")).toBeNull();
    expect(text(query(container, ".bj-scoreblock-numeral-row"))).toBe("72 / 100");
  });

  it("shows 'first -> final' only when a regeneration changed the score", () => {
    const { container, rerender } = render(<ScoreBlock label="ATS score" value={72} from={64} />);
    expect(text(query(container, ".bj-scoreblock-numeral-row"))).toBe("64 -> 72 / 100");
    rerender(<ScoreBlock label="ATS score" value={72} from={72} />);
    expect(container.querySelector(".bj-scoreblock-from")).toBeNull();
    rerender(<ScoreBlock label="ATS score" value={72} from={null} />);
    expect(container.querySelector(".bj-scoreblock-from")).toBeNull();
    rerender(<ScoreBlock label="ATS score" value={72} from={Number.NaN} />);
    expect(container.querySelector(".bj-scoreblock-from")).toBeNull();
  });

  it("can leave out the engine mark for a number that is not engine math, and the out-of can change", () => {
    const { container } = render(<ScoreBlock label="Fit" value={7.2} max={10} engine={false} />);
    expect(container.querySelector(".bj-scoreblock-engine")).toBeNull();
    expect(text(query(container, ".bj-scoreblock-numeral-row"))).toBe("7.2 / 10");
  });

  it("leaves out the body and the footer it was not given", () => {
    const { container } = render(<ScoreBlock label="ATS score" value={72} />);
    expect(container.querySelector(".bj-scoreblock-body")).toBeNull();
    expect(container.querySelector(".bj-scoreblock-footer")).toBeNull();
  });

  it("has no serious accessibility violations", async () => {
    const { container } = render(
      <main>
        <ScoreBlock label="ATS score" value={72} from={64} caption="medium confidence" footer="An honest number, not a verdict.">
          <SegmentedBar label="Semantic coverage" value={27} max={35} />
        </ScoreBlock>
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});

describe("FitLadder", () => {
  it("fills as many of the four cells as the rung reached, and says 'Good -- 61 / 100'", () => {
    const { container } = render(<FitLadder levels={LEVELS} level="Good" score={61} />);
    expect(container.querySelectorAll(".bj-ladder-cell")).toHaveLength(4);
    expect(filledCells(container)).toBe(3);
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Good -- 61 / 100");
    expect(text(query(container, ".bj-ladder-words"))).toBe("Good -- 61 / 100");
    expect(text(query(container, ".bj-ladder-caption"))).toBe("engine ✦");
  });

  it("fills one cell for the lowest rung and all four for the highest, never zero for a rung that was reached", () => {
    for (const [level, filled] of [["Poor", 1], ["Mixed", 2], ["Good", 3], ["Strong", 4]] as const) {
      const { container, unmount } = render(<FitLadder levels={LEVELS} level={level} score={50} />);
      expect(filledCells(container)).toBe(filled);
      unmount();
    }
  });

  it("is hollow, with the placeholder word and no number and no engine mark, for a fallback", () => {
    const { container } = render(<FitLadder levels={LEVELS} level="Good" score={61} placeholder />);
    expect(filledCells(container)).toBe(0);
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Placeholder");
    expect(container.textContent).not.toMatch(/\d/);
    expect(container.querySelector(".bj-ladder-caption")).toBeNull();
  });

  it("is hollow and says 'Not scored' for no rung, or a rung the ladder does not have, instead of guessing", () => {
    for (const level of [null, "Excellent"]) {
      const { container, unmount } = render(<FitLadder levels={LEVELS} level={level} score={90} />);
      expect(filledCells(container)).toBe(0);
      expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Not scored");
      unmount();
    }
  });

  it("leaves the number out of the words when the score is not a number", () => {
    const { container } = render(<FitLadder levels={LEVELS} level="Mixed" score={null} />);
    expect(text(query(container, ".bj-ladder-words"))).toBe("Mixed");
    expect(filledCells(container)).toBe(2);
  });

  it("can leave out the engine caption", () => {
    const { container } = render(<FitLadder levels={LEVELS} level="Good" score={61} engine={false} />);
    expect(container.querySelector(".bj-ladder-caption")).toBeNull();
  });

  it("has no serious accessibility violations", async () => {
    const { container } = render(
      <main>
        <FitLadder levels={LEVELS} level="Good" score={61} />
        <FitLadder levels={LEVELS} level={null} placeholder />
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});
