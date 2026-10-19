// @vitest-environment jsdom
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { importReducer, initialImportState, type ImportEvent, type ImportState } from "../lib/resumeImport";
import { parseImportResponse } from "../lib/resumeImportDraft";
import { allFields, buildReview, fieldsToCheck } from "../lib/resumeImportReview";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { ResumeImportView, type ResumeImportActions } from "./ResumeImportView";

// Checking every value one by one before a draft can be used: a checkbox on each value read from the
// file, a count of what is left, and "Use this profile" locked until none is. There is no "check all".

function draft(experience: Record<string, unknown>[] = [{ title: "Data Engineer", company: "Acme Corp", start_date: "2022-01", end_date: "present", is_current: true }]) {
  const parsed = parseImportResponse({
    version_id: "11111111-1111-1111-1111-111111111111",
    already_active: false,
    profile: {
      personal: { name: "Pat Example", emails: [{ address: "pat@example.com", primary: true }] },
      experience,
    },
    span_unit: "utf16",
    source_spans: {},
    dropped: [],
    assumptions: [],
    extracted_text: "Pat Example",
    stats: { kept_fields: 7, dropped_fields: 0 },
    warnings: [],
    document: { kind: "pdf", filename: "cv.pdf", pages_total: 1, pages_read: 1, truncated: false },
  });
  if (parsed === null) throw new Error("the fixture is not a draft");
  return parsed;
}

function reviewing(parsed = draft()): ImportState {
  const events: ImportEvent[] = [{ type: "file_chosen", fileName: "cv.pdf" }, { type: "upload_started" }, { type: "uploaded", draft: parsed }];
  return events.reduce(importReducer, initialImportState);
}

const TO_CHECK = fieldsToCheck(buildReview(draft().profile, draft().spans));

function actionsOf(): ResumeImportActions {
  const nothing = () => undefined;
  return { chooseFile: nothing, fileChosen: nothing, retry: nothing, close: nothing, toggleSource: nothing, askUse: vi.fn(), cancelUse: nothing, confirmUse: nothing, discard: nothing };
}

function Harness({ state = reviewing(), actions = actionsOf(), withChecks = true, replacesCurrent = true }: { state?: ImportState; actions?: ResumeImportActions; withChecks?: boolean; replacesCurrent?: boolean }) {
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const toggle = (path: string) =>
    setChecked((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  return (
    <MemoryRouter>
      <ResumeImportView state={state} replacesCurrent={replacesCurrent} actions={actions} checks={withChecks ? { checked, toggle } : undefined} trail={<p className="the-trail">Trail slot</p>} />
    </MemoryRouter>
  );
}

const boxes = (root: ParentNode) => [...root.querySelectorAll<HTMLInputElement>(".bj-import-check input")];
const use = (root: ParentNode) => buttonNamed(root, "Use this profile");

describe("which values are checked one by one", () => {
  it("is every value read from the file, and not the ones a rule set from other values", () => {
    const all = allFields(buildReview(draft().profile, draft().spans));
    const derived = all.filter((field) => field.derived);
    expect(derived.length).toBeGreaterThan(0);
    expect(TO_CHECK).toHaveLength(all.length - derived.length);
    expect(TO_CHECK.every((field) => !field.derived)).toBe(true);
  });
});

describe("the review with checks", () => {
  it("has the id the band's link lands on, and its heading takes focus when a link arrives", () => {
    const { container } = render(<Harness />);
    const review = query(container, "#import-review");
    expect(query(review, "h3").getAttribute("tabindex")).toBe("-1");
  });

  it("puts a checkbox on every value to check, named by the value, and none on a derived one", () => {
    const { container } = render(<Harness />);
    expect(boxes(container)).toHaveLength(TO_CHECK.length);
    expect(boxes(container).every((box) => !box.checked)).toBe(true);
    const names = [...container.querySelectorAll(".bj-import-check")].map((label) => text(label));
    expect(names).toContain("Checked Name");
    const titleLabel = TO_CHECK.find((field) => field.path === "/experience/0/title")?.label ?? "";
    expect(names.some((name) => name.startsWith(`Checked ${titleLabel} in `))).toBe(true);
    const derivedRow = [...container.querySelectorAll(".bj-import-field")].find((row) => text(row).includes("Set by a rule"));
    expect(derivedRow?.querySelector("input")).toBeNull();
  });

  it("names each checkbox and each source button by the entry it is in, so two entries do not have the same controls", () => {
    const two = draft([
      { title: "Data Engineer", company: "Acme Corp", start_date: "2022-01", end_date: "present", is_current: true },
      { title: "Analyst", company: "Beta LLC", start_date: "2019-01", end_date: "2021-12", is_current: false },
    ]);
    const { container } = render(<Harness state={reviewing(two)} />);
    const checkNames = [...container.querySelectorAll(".bj-import-check")].map((label) => text(label));
    expect(new Set(checkNames).size).toBe(checkNames.length);
    expect(checkNames).toContain("Checked Company in Experience 1: Data Engineer, Acme Corp");
    expect(checkNames).toContain("Checked Company in Experience 2: Analyst, Beta LLC");
    // the visible words are still "Checked": the name begins with them
    expect(checkNames.every((name) => name.startsWith("Checked"))).toBe(true);
  });

  it("draws the review's groups as plain groups, not as landmarks that repeat the page's own region names", () => {
    const { container } = render(<Harness />);
    expect(query(container, "#import-review").querySelectorAll("section[aria-labelledby]")).toHaveLength(1);
    expect(text(query(container, "#import-review .bj-import-section h4"))).toBe("Personal details");
  });

  it("says how many are left, in a polite status, and has no way to check them all at once", () => {
    const { container } = render(<Harness />);
    const status = query(container, "#bj-import-progress");
    expect(status.getAttribute("role")).toBe("status");
    expect(text(status)).toContain(`${TO_CHECK.length} of ${TO_CHECK.length} fields still to check`);
    expect(text(status)).toContain("there is no way to check them all at once");
    expect([...container.querySelectorAll("button")].some((button) => /all/i.test(text(button)) && /check/i.test(text(button)))).toBe(false);
  });

  it("locks 'Use this profile' while any value is unchecked: aria-disabled, a hint naming how many, and no question asked", () => {
    const actions = actionsOf();
    const { container } = render(<Harness actions={actions} />);
    const button = use(container);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    const hint = query(container, `#${button.getAttribute("aria-describedby")}`);
    expect(text(hint)).toBe(`Check the ${TO_CHECK.length} remaining fields to use this profile.`);
    click(button);
    expect(actions.askUse).not.toHaveBeenCalled();
  });

  it("counts down as values are checked, says the last one in the singular, and unlocks only when none is left", () => {
    const actions = actionsOf();
    const { container } = render(<Harness actions={actions} />);
    for (const box of boxes(container).slice(0, -1)) click(box);
    expect(text(query(container, "#bj-import-progress"))).toContain(`1 of ${TO_CHECK.length} fields still to check`);
    expect(use(container).getAttribute("aria-disabled")).toBe("true");
    expect(text(query(container, `#${use(container).getAttribute("aria-describedby")}`))).toBe("Check the 1 remaining field to use this profile.");
    click(boxes(container).at(-1) as HTMLInputElement);
    expect(text(query(container, "#bj-import-progress"))).toBe(`All ${TO_CHECK.length} fields checked.`);
    expect(use(container).hasAttribute("aria-disabled")).toBe(false);
    expect(use(container).hasAttribute("aria-describedby")).toBe(false);
    click(use(container));
    expect(actions.askUse).toHaveBeenCalledTimes(1);
  });

  it("locks again when a value is unchecked", () => {
    const { container } = render(<Harness />);
    for (const box of boxes(container)) click(box);
    expect(use(container).hasAttribute("aria-disabled")).toBe(false);
    click(boxes(container)[0]);
    expect(use(container).getAttribute("aria-disabled")).toBe("true");
  });

  it("keeps 'Use this profile' focusable while it is locked, so a keyboard user can find out why", () => {
    const { container } = render(<Harness />);
    expect(use(container).disabled).toBe(false);
  });

  it("is as it always was when the page keeps no check state: no boxes, no lock, no count", () => {
    const { container } = render(<Harness withChecks={false} />);
    expect(boxes(container)).toHaveLength(0);
    expect(container.querySelector("#bj-import-progress")).toBeNull();
    expect(use(container).hasAttribute("aria-disabled")).toBe(false);
  });

  it("says that using the draft replaces the current profile, only when there is one", () => {
    expect(text(render(<Harness replacesCurrent />).container)).toContain("Using it makes it your active profile in place of your current one, which stays in your history.");
    const first = render(<Harness replacesCurrent={false} />);
    expect(text(first.container)).not.toContain("in place of your current one, which stays");
  });

  it("draws the trail it was given under the notice, and no button of the card is a cyan fill", () => {
    const { container } = render(<Harness />);
    expect(text(query(container, ".the-trail"))).toBe("Trail slot");
    expect(container.querySelector(".bj-primary")).toBeNull();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = render(<Harness />);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("the other states of the card draw no cyan fill and no inline style", () => {
  const FAILED: ImportState = [
    { type: "file_chosen", fileName: "a.pdf" },
    { type: "upload_started" },
    { type: "upload_failed", failure: { kind: "error", message: "That PDF is password-protected." }, retry: true },
  ].reduce((state, event) => importReducer(state, event as ImportEvent), initialImportState);

  it.each([
    ["idle", initialImportState],
    ["a failed upload with a retry", FAILED],
  ])("%s", (_name, state) => {
    const { container } = render(<Harness state={state} />);
    expect(container.querySelector(".bj-primary")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
    const input = query<HTMLInputElement>(container, 'input[type="file"]');
    expect(input.hidden).toBe(true);
  });
});

describe("setup known to be missing", () => {
  const idle = (modelKey?: "none" | "needs_key" | "needs_model" | "ready", actions = actionsOf()) =>
    render(
      <MemoryRouter>
        <ResumeImportView state={initialImportState} replacesCurrent={false} actions={actions} modelKey={modelKey} />
      </MemoryRouter>,
    );

  it.each([
    ["none", "Add a model key"],
    ["needs_key", "Add a model key"],
    ["needs_model", "Choose a model"],
  ] as const)("says what is missing, with a link to where it is fixed, for a key reading of %s", (reading, label) => {
    const { container } = idle(reading);
    const state = query(container, ".bj-state--setup");
    expect(text(state)).toContain("Importing from a resume file uses your own AI model, so it needs a usable model key first.");
    const link = query<HTMLAnchorElement>(state, "a");
    expect(text(link)).toBe(label);
    expect(link.getAttribute("href")).toBe("/settings#ai-provider");
  });

  it("switches 'Choose a file' off and sends nothing while it is known to be missing, and keeps it focusable", () => {
    const actions = { ...actionsOf(), chooseFile: vi.fn() };
    const { container } = idle("none", actions);
    const choose = buttonNamed(container, "Choose a file");
    expect(choose.getAttribute("aria-disabled")).toBe("true");
    expect(choose.disabled).toBe(false);
    click(choose);
    expect(actions.chooseFile).not.toHaveBeenCalled();
  });

  it.each([["a usable key", "ready" as const], ["keys the page has not read", undefined]])("says nothing and leaves the card as it was for %s", (_name, reading) => {
    const actions = { ...actionsOf(), chooseFile: vi.fn() };
    const { container } = idle(reading, actions);
    expect(container.querySelector(".bj-state--setup")).toBeNull();
    const choose = buttonNamed(container, "Choose a file");
    expect(choose.hasAttribute("aria-disabled")).toBe(false);
    click(choose);
    expect(actions.chooseFile).toHaveBeenCalledTimes(1);
  });

  it("has no serious accessibility violation", async () => {
    const { container } = idle("none");
    await expectNoSeriousA11yViolations(container);
  });
});
