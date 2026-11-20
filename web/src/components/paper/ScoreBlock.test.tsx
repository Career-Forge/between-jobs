// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { GRADE_LADDER, type FitGrade } from "../../lib/fitGrade";
import { query, render } from "../../testing/render";
import { FitLadder, ScoreBlock } from "./ScoreBlock";
import { SegmentedBar } from "./SegmentedBar";

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
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
    expect(text(query(container, ".bj-scoreblock-numeral [aria-hidden]"))).toBe("64 -> 72");
    // What a screen reader gets instead of the arrow.
    expect(text(query(container, ".bj-scoreblock-numeral .bj-visually-hidden"))).toBe("was 64, now 72");
    expect(container.querySelectorAll(".bj-scoreblock-numeral > *:not([aria-hidden]):not(.bj-visually-hidden)")).toHaveLength(0);
    rerender(<ScoreBlock label="ATS score" value={72} from={72} />);
    expect(container.querySelector(".bj-scoreblock-from")).toBeNull();
    expect(container.querySelector(".bj-scoreblock-numeral .bj-visually-hidden")).toBeNull();
    expect(text(query(container, ".bj-scoreblock-numeral-row"))).toBe("72 / 100");
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
  const ladderClass = (root: ParentNode) => query(root, ".bj-ladder").className;

  it("fills as many of the three cells as the rung reached, and says 'Decent -- 61 / 100'", () => {
    const { container } = render(<FitLadder grade="decent" score={61} />);
    expect(container.querySelectorAll(".bj-ladder-cell")).toHaveLength(3);
    expect(filledCells(container)).toBe(2);
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Decent -- 61 / 100");
    expect(text(query(container, ".bj-ladder-words"))).toBe("Decent -- 61 / 100");
    expect(text(query(container, ".bj-ladder-caption"))).toBe("engine ✦");
  });

  it("fills one cell for the lowest rung and all three for the highest, never zero for a rung that was reached", () => {
    for (const [grade, filled] of [["bad", 1], ["decent", 2], ["good", 3]] as const) {
      const { container, unmount } = render(<FitLadder grade={grade} score={50} />);
      expect(filledCells(container)).toBe(filled);
      unmount();
    }
  });

  it("names each rung by the grade's word: Bad fit, Decent, Good", () => {
    for (const [grade, word] of [["bad", "Bad fit"], ["decent", "Decent"], ["good", "Good"]] as const) {
      const { container, unmount } = render(<FitLadder grade={grade} score={50} />);
      expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe(`Fit: ${word} -- 50 / 100`);
      expect(text(query(container, ".bj-ladder-words"))).toBe(`${word} -- 50 / 100`);
      unmount();
    }
  });

  it("carries the grade as a class, which is what colours the filled cells, and always has the word beside the colour", () => {
    for (const grade of GRADE_LADDER) {
      const { container, unmount } = render(<FitLadder grade={grade} score={50} showWords={false} />);
      expect(ladderClass(container).split(" ")).toContain(`bj-ladder--${grade}`);
      expect(ladderClass(container).split(" ").filter((name) => name.startsWith("bj-ladder--"))).toHaveLength(1);
      // The words can be left to the caller, but the image is never a colour alone: its label says the grade.
      expect(query(container, '[role="img"]').getAttribute("aria-label")).not.toBe("");
      unmount();
    }
  });

  it("is hollow, with the placeholder word and no number, no grade class and no engine mark, for a fallback", () => {
    const { container } = render(<FitLadder grade="decent" score={61} placeholder />);
    expect(filledCells(container)).toBe(0);
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Placeholder");
    expect(container.textContent).not.toMatch(/\d/);
    expect(container.querySelector(".bj-ladder-caption")).toBeNull();
    expect(ladderClass(container)).toBe("bj-ladder");
  });

  it("is hollow and says 'Not scored' for no rung, or for anything that is not a grade, instead of guessing", () => {
    // An engine bin is not a grade either: the caller turns a bin into a grade (lib/fitGrade.ts) before it gets here.
    for (const grade of [null, "Excellent", "Strong", "Good", "Bad fit", "constructor", "good bj-primary"]) {
      const { container, unmount } = render(<FitLadder grade={grade as FitGrade | null} score={90} />);
      expect(filledCells(container), String(grade)).toBe(0);
      expect(query(container, '[role="img"]').getAttribute("aria-label"), String(grade)).toBe("Fit: Not scored");
      // Nothing was scored, so code did no math: no "engine" caption either.
      expect(container.querySelector(".bj-ladder-caption")).toBeNull();
      expect(container.textContent).not.toContain("engine");
      // And nothing of the value given is turned into a class.
      expect(ladderClass(container), String(grade)).toBe("bj-ladder");
      unmount();
    }
  });

  it("leaves the number out of the words when the score is not a number", () => {
    const { container } = render(<FitLadder grade="bad" score={null} />);
    expect(text(query(container, ".bj-ladder-words"))).toBe("Bad fit");
    expect(filledCells(container)).toBe(1);
  });

  it("leaves the number out of the words and of the label when it is outside the scale, and keeps the rung", () => {
    for (const score of [150, -5, 100.5, Number.NaN, Number.POSITIVE_INFINITY]) {
      const { container, unmount } = render(<FitLadder grade="good" score={score} />);
      expect(query(container, '[role="img"]').getAttribute("aria-label"), String(score)).toBe("Fit: Good");
      expect(text(query(container, ".bj-ladder-words")), String(score)).toBe("Good");
      expect(filledCells(container)).toBe(3);
      unmount();
    }
  });

  it("says a score at either end of the scale, and one measured against a different top", () => {
    for (const [score, words] of [[0, "Bad fit -- 0 / 100"], [100, "Bad fit -- 100 / 100"], [61.5, "Bad fit -- 61.5 / 100"]] as const) {
      const { container, unmount } = render(<FitLadder grade="bad" score={score} />);
      expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe(`Fit: ${words}`);
      unmount();
    }
    const { container } = render(<FitLadder grade="decent" score={36} max={35} />);
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Decent");
  });

  it("can leave out the words, and keeps the image's label and the engine mark", () => {
    const { container } = render(<FitLadder grade="good" score={88} showWords={false} className="bj-disc-ladder" />);
    expect(container.querySelector(".bj-ladder-words")).toBeNull();
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Good -- 88 / 100");
    expect(filledCells(container)).toBe(3);
    expect(text(query(container, ".bj-ladder-caption"))).toBe("engine ✦");
    expect(query(container, ".bj-ladder").classList.contains("bj-disc-ladder")).toBe(true);
    expect(query(container, ".bj-ladder").classList.contains("bj-ladder--good")).toBe(true);
  });

  it("can leave out the words of a placeholder too, and still labels it", () => {
    const { container } = render(<FitLadder grade={null} placeholder showWords={false} />);
    expect(container.querySelector(".bj-ladder-words")).toBeNull();
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Placeholder");
  });

  it("draws the words by default", () => {
    const { container } = render(<FitLadder grade="decent" score={61} />);
    expect(container.querySelector(".bj-ladder-words")).not.toBeNull();
  });

  it("can leave out the engine caption", () => {
    const { container } = render(<FitLadder grade="decent" score={61} engine={false} />);
    expect(container.querySelector(".bj-ladder-caption")).toBeNull();
  });

  it("has no serious accessibility violations", async () => {
    const { container } = render(
      <main>
        <FitLadder grade="good" score={88} />
        <FitLadder grade="decent" score={61} />
        <FitLadder grade="bad" score={20} />
        <FitLadder grade={null} placeholder />
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});
