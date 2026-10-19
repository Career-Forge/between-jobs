// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { ChecklistItem } from "../lib/generateTypes";
import type { ChecklistState } from "../lib/useGeneration";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { text } from "../testing/forms";
import { buttonNamed, click, keydown, query, render } from "../testing/render";
import { ExportChecklist } from "./ExportChecklist";

const item = (key: string, status: ChecklistItem["status"], label = key, detail = `${key} detail`): ChecklistItem => ({ key, status, label, detail });

// One flagged claim and no engine cautions: one fail, five passes, four honestly not checked.
const ITEMS: ChecklistItem[] = [
  item("one_page", "pass"),
  item("no_engine_warnings", "pass"),
  item("no_unsupported_claims", "fail"),
  item("page_fill", "pass"),
  item("page_count_within_shape", "pass"),
  item("pins_honored", "pass"),
  item("links_valid", "not_checked", "Links work", "Open each link yourself."),
  item("text_selectable", "not_checked"),
  item("fonts_embedded", "not_checked"),
  item("no_duplicate_bullets", "not_checked"),
];

const ready = (items: ChecklistItem[] = ITEMS): ChecklistState => ({ kind: "ready", items });

describe("before the checks have run", () => {
  it("asks for them with a button, says they recompile the resume, and runs nothing by itself", () => {
    const onRun = vi.fn();
    const { container } = render(<ExportChecklist checklist={{ kind: "idle" }} onRun={onRun} />);
    expect(text(container)).toContain("It recompiles your resume, so it runs only when you ask.");
    expect(onRun).not.toHaveBeenCalled();
    click(buttonNamed(container, "Run the export checks"));
    expect(onRun).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".bj-trail")).toBeNull();
  });

  it("holds still while running: Checking..., aria-disabled, a press does nothing", () => {
    const onRun = vi.fn();
    const { container } = render(<ExportChecklist checklist={{ kind: "loading" }} onRun={onRun} />);
    const button = buttonNamed(container, "Checking...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    click(button);
    expect(onRun).not.toHaveBeenCalled();
    expect(container.querySelector("[role='status'][aria-busy='true']")).not.toBeNull();
  });

  it("names a failure with a Retry that runs them again", () => {
    const onRun = vi.fn();
    const { container } = render(<ExportChecklist checklist={{ kind: "error", message: "Failed to load checklist" }} onRun={onRun} />);
    expect(text(query(container, "[role='alert']"))).toContain("Failed to load checklist");
    click(buttonNamed(container, "Retry"));
    expect(onRun).toHaveBeenCalledTimes(1);
  });
});

describe("the result", () => {
  it("counts what code ran and what is the person's: 5 pass · 1 fail · 4 not checked, in two named groups", () => {
    const { container } = render(<ExportChecklist checklist={ready()} onRun={() => undefined} />);
    expect(text(query(container, ".bj-checkgroup-summary"))).toBe("5 pass · 1 fail · 4 not checked");
    expect([...container.querySelectorAll(".bj-checkgroup-title")].map((node) => text(node))).toEqual(["Checked by Between Jobs (6)", "Yours to check (4)"]);
    const states = (label: string) => [...container.querySelectorAll(`ul[aria-label="${label}"] .bj-checkrow-state`)].map((node) => text(node));
    expect(states("Checked by Between Jobs")).toEqual(["✓ Pass", "✓ Pass", "✕ Fail", "✓ Pass", "✓ Pass", "✓ Pass"]);
    expect(new Set(states("Yours to check"))).toEqual(new Set(["○ Not checked"]));
    expect(text(container)).toContain("Open each link yourself.");
  });

  it("is a trail that says code, no model", () => {
    const { container } = render(<ExportChecklist checklist={ready()} onRun={() => undefined} />);
    expect(text(query(container, ".bj-trail"))).toContain("Checked by code · no model involved");
  });

  it("puts a not-checked claim check in the person's group when the engine does not check claims, and counts it as not checked", () => {
    const items = ITEMS.map((entry) => (entry.key === "no_unsupported_claims" ? { ...entry, status: "not_checked" as const } : entry));
    const { container } = render(<ExportChecklist checklist={ready(items)} onRun={() => undefined} />);
    expect(text(query(container, ".bj-checkgroup-summary"))).toBe("5 pass · 0 fail · 5 not checked");
    expect([...container.querySelectorAll(".bj-checkgroup-title")].map((node) => text(node))).toEqual(["Checked by Between Jobs (5)", "Yours to check (5)"]);
  });

  it("says the one-page check assumes one page when it fails while the shape check passes", () => {
    const items = ITEMS.map((entry) => (entry.key === "one_page" ? { ...entry, status: "fail" as const, detail: "2 pages." } : entry));
    const { container } = render(<ExportChecklist checklist={ready(items)} onRun={() => undefined} />);
    expect(text(container)).toContain("This check assumes one page; your settings allow two.");
    const both = ITEMS.map((entry) => (entry.key === "one_page" || entry.key === "page_count_within_shape" ? { ...entry, status: "fail" as const } : entry));
    const other = render(<ExportChecklist checklist={ready(both)} onRun={() => undefined} />);
    expect(text(other.container)).not.toContain("assumes one page");
  });

  it("offers a way to run them again, which is the same request", () => {
    const onRun = vi.fn();
    const { container } = render(<ExportChecklist checklist={ready()} onRun={onRun} />);
    click(buttonNamed(container, "Check again"));
    expect(onRun).toHaveBeenCalledTimes(1);
  });
});

describe("the person's own marks", () => {
  it("are off by default, pressed with the keyboard like any button, and use the shared selection state", () => {
    const { container } = render(<ExportChecklist checklist={ready()} onRun={() => undefined} />);
    const marks = [...container.querySelectorAll("button.bj-selectable")] as HTMLButtonElement[];
    expect(marks).toHaveLength(4);
    for (const mark of marks) expect(mark.getAttribute("aria-pressed")).toBe("false");
    marks[0].focus();
    expect(keydown("Enter", { target: marks[0] })).toBe(true);
    click(marks[0]);
    expect(marks[0].getAttribute("aria-pressed")).toBe("true");
    expect(text(marks[0])).toBe("● I checked this");
    click(marks[0]);
    expect(marks[0].getAttribute("aria-pressed")).toBe("false");
  });

  it("are never counted: ticking every one leaves the summary and the states as the server said, and the page says they are not saved", () => {
    const { container } = render(<ExportChecklist checklist={ready()} onRun={() => undefined} />);
    for (const mark of container.querySelectorAll("button.bj-selectable")) click(mark);
    expect(text(query(container, ".bj-checkgroup-summary"))).toBe("5 pass · 1 fail · 4 not checked");
    expect(container.querySelectorAll(".bj-checkrow--not_checked")).toHaveLength(4);
    expect(text(container)).toContain("They are not saved, and Between Jobs does not count them as checked.");
  });

  it("only the buttons take the keyboard: a row is not a tab stop and there is no tick box", async () => {
    const { container } = render(<ExportChecklist checklist={ready()} onRun={() => undefined} />);
    expect(container.querySelectorAll("input[type='checkbox']")).toHaveLength(0);
    expect(container.querySelectorAll(".bj-checkrow[tabindex]")).toHaveLength(0);
    await expectNoSeriousA11yViolations(container);
  });
});
