// @vitest-environment jsdom
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { SECTION_FIELD_CONFIGS } from "../lib/sectionFieldConfigs";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { typeInto } from "../testing/forms";
import { buttonNamed, click, keydown, query, render } from "../testing/render";
import { EntryEditModal } from "./EntryEditModal";

// The entry editor is a real dialog: labelled, focus kept inside, Esc cancels, focus returns, every
// control named. The versioned save itself is SectionedProfile's (SectionedProfile.test.tsx).

const dialog = () => query(document.body, '[role="dialog"]');

function Page(props: Partial<Parameters<typeof EntryEditModal>[0]> & { onSave?: (values: Record<string, unknown>) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <main>
      <button type="button" onClick={() => setOpen(true)}>
        Edit experience
      </button>
      {open && (
        <EntryEditModal
          config={SECTION_FIELD_CONFIGS.experience}
          initialValues={{ title: "Data Engineer", company: "Acme", start_date: "2022-01", end_date: "present", is_current: true, bullets: ["Built pipelines", "Cut costs"], skills: ["Python"] }}
          isNew={false}
          canPin
          pinnedCountExcludingThis={0}
          versionLabel="Saving creates version 4"
          saving={false}
          error={null}
          onSave={props.onSave ?? (() => undefined)}
          onCancel={() => setOpen(false)}
          {...props}
        />
      )}
    </main>
  );
}

function open() {
  const view = render(<Page />);
  const opener = buttonNamed(view.container, "Edit experience");
  opener.focus();
  click(opener);
  return { ...view, opener };
}

describe("the entry dialog", () => {
  it("is a labelled modal dialog named by its heading, drawn outside the page so the page can be inert", () => {
    const { container } = open();
    const box = dialog();
    expect(box.getAttribute("aria-modal")).toBe("true");
    expect(box.getAttribute("aria-labelledby")).toBe(query(box, "h2").id);
    expect(query(box, "h2").textContent).toBe("Edit experience");
    expect(container.contains(box)).toBe(false);
    expect(container.hasAttribute("inert")).toBe(true);
  });

  it("says it adds when the entry is new", () => {
    render(<Page isNew initialValues={{}} />);
    click(buttonNamed(document.body, "Edit experience"));
    expect(query(dialog(), "h2").textContent).toBe("Add experience");
  });

  it("starts focus on the first field, not on the close mark", () => {
    open();
    expect(document.activeElement).toBe(query(dialog(), 'input[type="text"]'));
    expect((document.activeElement as HTMLInputElement).value).toBe("Data Engineer");
  });

  it("keeps Tab inside: from the last control it wraps to the first, and Shift+Tab from the first wraps to the last", () => {
    open();
    const box = dialog();
    const stops = [...box.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])")];
    stops[stops.length - 1].focus();
    expect(keydown("Tab")).toBe(false);
    expect(document.activeElement).toBe(stops[0]);
    expect(keydown("Tab", { shift: true })).toBe(false);
    expect(document.activeElement).toBe(stops[stops.length - 1]);
  });

  it("cancels on Esc and gives focus back to the control that opened it", () => {
    const { opener } = open();
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("cancels from Cancel and from the close mark, and neither saves", () => {
    const onSave = vi.fn();
    const view = render(<Page onSave={onSave} />);
    click(buttonNamed(view.container, "Edit experience"));
    click(buttonNamed(dialog(), "Cancel"));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    click(buttonNamed(view.container, "Edit experience"));
    click(buttonNamed(dialog(), "Close"));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not close on Esc, and turns Cancel and Close off, while a save is out", () => {
    render(<Page saving />);
    click(buttonNamed(document.body, "Edit experience"));
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(buttonNamed(dialog(), "Cancel").disabled).toBe(true);
    expect(buttonNamed(dialog(), "Close").disabled).toBe(true);
    // Save is aria-disabled, never disabled: pressing it started the request, and a button that disables itself
    // drops keyboard focus out of the dialog, where a refused save would leave it.
    const saving = buttonNamed(dialog(), "Saving...");
    expect(saving.disabled).toBe(false);
    expect(saving.getAttribute("aria-disabled")).toBe("true");
  });

  it("keeps keyboard focus on Save while the save is out, and a press while it is does nothing", () => {
    const onSave = vi.fn();
    const view = render(<Page onSave={onSave} />);
    click(buttonNamed(view.container, "Edit experience"));
    const save = buttonNamed(dialog(), "Save");
    save.focus();
    view.rerender(<Page onSave={onSave} saving />);
    expect(buttonNamed(dialog(), "Saving...")).toBe(save);
    expect(document.activeElement).toBe(save);
    click(save);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("says what saving does: it makes the next version", () => {
    open();
    expect(dialog().textContent).toContain("Saving creates version 4");
  });

  it("hands the edited entry to onSave", () => {
    const onSave = vi.fn();
    const view = render(<Page onSave={onSave} />);
    click(buttonNamed(view.container, "Edit experience"));
    typeInto(query(dialog(), 'input[type="text"]'), "Staff Data Engineer");
    click(buttonNamed(dialog(), "Save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toMatchObject({ title: "Staff Data Engineer", company: "Acme" });
  });

  it("keeps Save off until the required fields have something in them", () => {
    render(<Page initialValues={{ title: "", company: "" }} />);
    click(buttonNamed(document.body, "Edit experience"));
    const off = () => buttonNamed(dialog(), "Save").getAttribute("aria-disabled") === "true";
    expect(off()).toBe(true);
    const [title, company] = [...dialog().querySelectorAll<HTMLInputElement>('input[type="text"]')];
    typeInto(title, "Engineer");
    expect(off()).toBe(true);
    typeInto(company, "Acme");
    expect(off()).toBe(false);
  });

  it("shows a failed save as an alert inside the dialog, which stays open", () => {
    render(<Page error="Your profile must keep at least one piece of evidence." />);
    click(buttonNamed(document.body, "Edit experience"));
    const alert = query(dialog(), '[role="alert"]');
    expect(alert.textContent).toBe("Your profile must keep at least one piece of evidence.");
  });

  it("is never a cyan fill: Save is an ordinary outlined button", () => {
    open();
    expect(dialog().querySelector(".bj-primary")).toBeNull();
    expect(buttonNamed(dialog(), "Save").className).toBe("bj-dialog-confirm");
  });

  it("names every control: bullets, their moves and removals, chips, and the two ends of the date range", () => {
    open();
    const box = dialog();
    for (const name of [
      "Bullets 1",
      "Bullets 2",
      "Move bullets 1 up",
      "Move bullets 1 down",
      "Remove bullets 2",
      "Remove Python",
      "Add to skills",
      "Start month",
      "Start year",
      "End month",
      "End year",
    ]) {
      expect(box.querySelector(`[aria-label="${name}"]`), name).not.toBeNull();
    }
  });

  it("moves a bullet down with the button, and removes one", () => {
    const onSave = vi.fn();
    const view = render(<Page onSave={onSave} />);
    click(buttonNamed(view.container, "Edit experience"));
    click(buttonNamed(dialog(), "Move bullets 1 down"));
    click(buttonNamed(dialog(), "Remove bullets 2"));
    click(buttonNamed(dialog(), "Save"));
    expect(onSave.mock.calls[0][0]).toMatchObject({ bullets: ["Cut costs"] });
  });

  it("keeps the keyboard's place when a bullet is moved, removed or added, and when a chip is removed", () => {
    open();
    const box = dialog();
    // moved down: the same button on the row it moved to; at the end it stays and does nothing
    const down = buttonNamed(box, "Move bullets 1 down");
    down.focus();
    click(down);
    expect(document.activeElement).toBe(buttonNamed(box, "Move bullets 2 down"));
    expect(buttonNamed(box, "Move bullets 2 down").getAttribute("aria-disabled")).toBe("true");
    expect(buttonNamed(box, "Move bullets 2 down").disabled).toBe(false);
    // added: the new bullet's text box
    click(buttonNamed(box, "+ Add bullet"));
    expect(document.activeElement).toBe(box.querySelector('textarea[aria-label="Bullets 3"]'));
    // removed: the bullet that takes its place
    const remove = buttonNamed(box, "Remove bullets 2");
    remove.focus();
    click(remove);
    expect(document.activeElement).toBe(box.querySelector('textarea[aria-label="Bullets 2"]'));
    // removing the last one leaves the previous bullet
    click(buttonNamed(box, "Remove bullets 2"));
    expect(document.activeElement).toBe(box.querySelector('textarea[aria-label="Bullets 1"]'));
    click(buttonNamed(box, "Remove bullets 1"));
    expect(document.activeElement).toBe(buttonNamed(box, "+ Add bullet"));
    // a chip's remove button goes with the chip: focus goes to the box that adds one
    const chip = buttonNamed(box, "Remove Python");
    chip.focus();
    click(chip);
    expect(document.activeElement).toBe(box.querySelector('input[aria-label="Add to skills"]'));
  });

  it("sets and clears the end of a role with Current", () => {
    const onSave = vi.fn();
    const view = render(<Page onSave={onSave} initialValues={{ title: "Engineer", company: "Acme", start_date: "2022-01", end_date: "2023-05", is_current: false }} />);
    click(buttonNamed(view.container, "Edit experience"));
    const current = query<HTMLInputElement>(dialog(), ".bj-current-checkbox input");
    click(current);
    click(buttonNamed(dialog(), "Save"));
    expect(onSave.mock.calls[0][0]).toMatchObject({ is_current: true, end_date: "present" });
  });

  it("limits mandatory entries and says so", () => {
    render(<Page pinnedCountExcludingThis={6} initialValues={{ title: "A", company: "B" }} />);
    click(buttonNamed(document.body, "Edit experience"));
    expect(dialog().textContent).toContain("You've used all 6 mandatory slots");
    const box = query<HTMLInputElement>(dialog(), ".bj-pin-field input[type='checkbox']");
    expect(box.disabled).toBe(true);
  });

  it("offers no mandatory toggle for a section that cannot be pinned", () => {
    render(<Page canPin={false} />);
    click(buttonNamed(document.body, "Edit experience"));
    expect(dialog().querySelector(".bj-pin-field")).toBeNull();
  });

  it("has no serious accessibility violation", async () => {
    open();
    await expectNoSeriousA11yViolations(document.body);
  });
});

