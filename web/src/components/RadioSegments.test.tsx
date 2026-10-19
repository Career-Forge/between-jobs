// @vitest-environment jsdom
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { click, keydown, query, render } from "../testing/render";
import { RadioSegments } from "./RadioSegments";

// A radio group drawn as outlined segments: the chosen one carries aria-checked, one tab stop, and the
// arrow keys move and choose, as the ARIA radio pattern says.

const OPTIONS = [
  { value: "balanced", label: "Balanced" },
  { value: "compact", label: "Compact" },
  { value: "spacious", label: "Spacious" },
] as const;

function Harness({ initial = "balanced", onChange = () => undefined }: { initial?: string; onChange?: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <div>
      <span id="density-label">Density</span>
      <RadioSegments
        labelledBy="density-label"
        value={value}
        options={OPTIONS as unknown as readonly { value: string; label: string }[]}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
      />
    </div>
  );
}

const radios = (root: ParentNode) => [...root.querySelectorAll<HTMLButtonElement>('[role="radio"]')];

describe("RadioSegments", () => {
  it("is a radio group named by its label, with the chosen segment checked and the selection primitive on every segment", () => {
    const { container } = render(<Harness />);
    const group = query(container, '[role="radiogroup"]');
    expect(group.getAttribute("aria-labelledby")).toBe("density-label");
    expect(group.classList.contains("bj-segmented")).toBe(true);
    expect(radios(group).map((radio) => [radio.textContent, radio.getAttribute("aria-checked")])).toEqual([
      ["Balanced", "true"],
      ["Compact", "false"],
      ["Spacious", "false"],
    ]);
    expect(radios(group).every((radio) => radio.classList.contains("bj-selectable"))).toBe(true);
  });

  it("has one tab stop: the chosen segment, or the first when none is chosen", () => {
    const { container } = render(<Harness initial="spacious" />);
    expect(radios(container).map((radio) => radio.tabIndex)).toEqual([-1, -1, 0]);
    const none = render(<Harness initial="none" />);
    expect(radios(none.container).map((radio) => radio.tabIndex)).toEqual([0, -1, -1]);
  });

  it("chooses a segment when it is pressed", () => {
    const onChange = vi.fn();
    const { container } = render(<Harness onChange={onChange} />);
    click(radios(container)[1]);
    expect(onChange).toHaveBeenCalledWith("compact");
    expect(radios(container).map((radio) => radio.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
  });

  it("does not say a press on the chosen segment changed anything", () => {
    const onChange = vi.fn();
    const { container } = render(<Harness onChange={onChange} />);
    click(radios(container)[0]);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("moves to the next segment and chooses it on Arrow Right and Arrow Down, wrapping at the end", () => {
    const onChange = vi.fn();
    const { container } = render(<Harness initial="spacious" onChange={onChange} />);
    radios(container)[2].focus();
    keydown("ArrowRight");
    expect(onChange).toHaveBeenLastCalledWith("balanced");
    expect(document.activeElement).toBe(radios(container)[0]);
    keydown("ArrowDown");
    expect(onChange).toHaveBeenLastCalledWith("compact");
    expect(document.activeElement).toBe(radios(container)[1]);
  });

  it("moves back on Arrow Left and Arrow Up, wrapping at the start", () => {
    const onChange = vi.fn();
    const { container } = render(<Harness onChange={onChange} />);
    radios(container)[0].focus();
    keydown("ArrowLeft");
    expect(onChange).toHaveBeenLastCalledWith("spacious");
    expect(document.activeElement).toBe(radios(container)[2]);
    keydown("ArrowUp");
    expect(onChange).toHaveBeenLastCalledWith("compact");
  });

  it("goes to the first and last segment on Home and End", () => {
    const onChange = vi.fn();
    const { container } = render(<Harness initial="compact" onChange={onChange} />);
    radios(container)[1].focus();
    keydown("End");
    expect(onChange).toHaveBeenLastCalledWith("spacious");
    keydown("Home");
    expect(onChange).toHaveBeenLastCalledWith("balanced");
  });

  it("leaves other keys alone", () => {
    const onChange = vi.fn();
    const { container } = render(<Harness onChange={onChange} />);
    radios(container)[0].focus();
    expect(keydown("a")).toBe(true);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = render(<Harness />);
    await expectNoSeriousA11yViolations(container);
  });
});
