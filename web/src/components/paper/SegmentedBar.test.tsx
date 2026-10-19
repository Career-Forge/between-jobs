// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { barState } from "../../lib/scoreBreakdown";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { query, render } from "../../testing/render";
import { SegmentedBar } from "./SegmentedBar";

const cellsOf = (root: ParentNode) => [...root.querySelectorAll(".bj-segbar-cell")];
const kinds = (root: ParentNode) =>
  cellsOf(root).map((cell) => (cell.classList.contains("bj-segbar-cell--filled") ? "filled" : cell.classList.contains("bj-segbar-cell--hatched") ? "hatched" : "empty"));
const count = (list: string[], kind: string) => list.filter((entry) => entry === kind).length;

describe("SegmentedBar", () => {
  it("draws one cell per point and fills as many as the value (27 of 35)", () => {
    const { container } = render(<SegmentedBar label="Semantic coverage" value={27} max={35} />);
    const drawn = kinds(container);
    expect(drawn).toHaveLength(35);
    expect(count(drawn, "filled")).toBe(27);
    expect(drawn.slice(0, 27).every((kind) => kind === "filled")).toBe(true);
    expect(count(drawn, "hatched")).toBe(0);
  });

  it("adds up for a sample resume: 27 + 22 + 12 + 6 + 5 = 72 filled cells over five bars", () => {
    const rows: [string, number, number][] = [
      ["Semantic coverage", 27, 35],
      ["Experience quality", 22, 30],
      ["Hard requirements", 12, 15],
      ["Quantified impact", 6, 10],
      ["Company alignment · structure", 5, 10],
    ];
    const { container } = render(
      <div>
        {rows.map(([label, value, max]) => (
          <SegmentedBar key={label} label={label} value={value} max={max} />
        ))}
      </div>,
    );
    expect(count(kinds(container), "filled")).toBe(72);
    expect(cellsOf(container)).toHaveLength(100);
  });

  it("is a meter with the label as its name, the number out of the maximum, and the words as its value text", () => {
    const { container } = render(<SegmentedBar label="Semantic coverage" value={27} max={35} valueText="27 / 35 · near max" />);
    const meter = query(container, '[role="meter"]');
    expect(meter.getAttribute("aria-valuenow")).toBe("27");
    expect(meter.getAttribute("aria-valuemin")).toBe("0");
    expect(meter.getAttribute("aria-valuemax")).toBe("35");
    expect(meter.getAttribute("aria-valuetext")).toBe("27 / 35 · near max");
    const labelId = meter.getAttribute("aria-labelledby") ?? "";
    expect(container.querySelector(`#${CSS.escape(labelId)}`)?.textContent).toBe("Semantic coverage");
    expect(query(container, ".bj-segbar-value").textContent).toBe("27 / 35 · near max");
  });

  it("says its value in words by default, and trims a decimal", () => {
    const { container, rerender } = render(<SegmentedBar label="Fit" value={61} max={100} />);
    expect(query(container, ".bj-segbar-value").textContent).toBe("61 / 100");
    rerender(<SegmentedBar label="Fit" value={61.25} max={100} />);
    expect(query(container, ".bj-segbar-value").textContent).toBe("61.3 / 100");
  });

  it("works with the label scoreBreakdown.ts chooses: it never picks one by hand", () => {
    const value = 27;
    const max = 35;
    const { container } = render(<SegmentedBar label="Semantic coverage" value={value} max={max} valueText={`${value} / ${max} · ${barState(value, max)}`} />);
    expect(query(container, ".bj-segbar-value").textContent).toBe("27 / 35 · near max");
  });

  it("draws a /100 sub-score in twenty cells unless told otherwise", () => {
    const { container, rerender } = render(<SegmentedBar label="Skills" value={61} max={100} />);
    expect(cellsOf(container)).toHaveLength(20);
    expect(count(kinds(container), "filled")).toBe(12);
    rerender(<SegmentedBar label="Skills" value={61} max={100} segments={10} />);
    expect(cellsOf(container)).toHaveLength(10);
    expect(count(kinds(container), "filled")).toBe(6);
  });

  it("never draws a partial value as an empty bar or a full one", () => {
    const { container, rerender } = render(<SegmentedBar label="Skills" value={1} max={100} />);
    expect(count(kinds(container), "filled")).toBe(1);
    rerender(<SegmentedBar label="Skills" value={99} max={100} />);
    expect(count(kinds(container), "filled")).toBe(19);
    rerender(<SegmentedBar label="Skills" value={100} max={100} />);
    expect(count(kinds(container), "filled")).toBe(20);
  });

  it("draws exact headroom as hatched cells after the filled ones, never solid", () => {
    const { container } = render(<SegmentedBar label="Experience quality" value={18} max={30} hatched={4} valueText="18 / 30 · near max" />);
    const drawn = kinds(container);
    expect(drawn.slice(0, 18).every((kind) => kind === "filled")).toBe(true);
    expect(drawn.slice(18, 22).every((kind) => kind === "hatched")).toBe(true);
    expect(drawn.slice(22).every((kind) => kind === "empty")).toBe(true);
    // The meter still says the earned value, not earned plus headroom.
    expect(query(container, '[role="meter"]').getAttribute("aria-valuenow")).toBe("18");
  });

  it("is a hollow image with its reason in words when there is no value, and is never read as zero", () => {
    const { container } = render(<SegmentedBar label="Company Health" value={null} max={100} />);
    expect(container.querySelector('[role="meter"]')).toBeNull();
    const image = query(container, '[role="img"]');
    expect(image.getAttribute("aria-label")).toBe("Company Health: Not scored");
    expect(count(kinds(container), "filled")).toBe(0);
    expect(count(kinds(container), "hatched")).toBe(0);
  });

  it("takes the words for a missing value from the page", () => {
    const { container } = render(<SegmentedBar label="Company Health" value={null} max={100} valueText="Not applied: company isn't in the tier list" />);
    expect(query(container, '[role="img"]').getAttribute("aria-label")).toBe("Company Health: Not applied: company isn't in the tier list");
    expect(query(container, ".bj-segbar-value").textContent).toBe("Not applied: company isn't in the tier list");
  });

  it("treats an unusable maximum as no value rather than drawing a bar that says something", () => {
    const { container } = render(<SegmentedBar label="Odd" value={5} max={0} segments={4} />);
    expect(container.querySelector('[role="meter"]')).toBeNull();
    expect(count(kinds(container), "filled")).toBe(0);
  });

  it("keeps the meter's value inside its range", () => {
    const { container } = render(<SegmentedBar label="Over" value={50} max={35} />);
    expect(query(container, '[role="meter"]').getAttribute("aria-valuenow")).toBe("35");
    expect(count(kinds(container), "filled")).toBe(35);
  });

  it("keeps the caption outside the meter, where assistive tech can still read it", () => {
    const { container } = render(<SegmentedBar label="Quantified impact" value={6} max={10} detail="Judged by your model: two of five bullets carry a number." />);
    const caption = query(container, ".bj-segbar-detail");
    expect(query(container, '[role="meter"]').contains(caption)).toBe(false);
    expect(caption.textContent).toContain("two of five bullets");
  });

  it("hides the cells from assistive tech: the words carry the value", () => {
    const { container } = render(<SegmentedBar label="Semantic coverage" value={27} max={35} />);
    expect(query(container, ".bj-segbar-cells").getAttribute("aria-hidden")).toBe("true");
  });

  it("has no serious accessibility violations, with a value and without", async () => {
    const { container } = render(
      <div>
        <SegmentedBar label="Semantic coverage" value={27} max={35} detail="Eight of eleven clusters." />
        <SegmentedBar label="Company Health" value={null} max={100} />
      </div>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});
