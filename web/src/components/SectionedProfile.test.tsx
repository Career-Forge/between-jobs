// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CanonicalProfile } from "../lib/profileTypes";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { settle, text, typeInto } from "../testing/forms";
import { buttonNamed, click, keydown, query, render } from "../testing/render";
import { SectionedProfile } from "./SectionedProfile";

vi.mock("../lib/api", () => ({ apiFetch: vi.fn(), ApiError: class ApiError extends Error {} }));

// The sections of the profile and the ways of changing them: each change is one versioned save through
// `onSave` (the editor's), a failed one is always shown, and a section with nothing in it is not drawn.

const PROFILE: CanonicalProfile = {
  personal: { name: "Asha Verma" },
  experience: [
    { title: "Senior Data Engineer", company: "Acme Analytics", start_date: "2022-06", end_date: "present", is_current: true, bullets: ["Cut refresh time to 90 minutes."], skills: ["Python"], metrics: ["90% faster"], pin: { mandatory: true, min_bullets: 2 } },
    { title: "Data Analyst", company: "Orbit", start_date: "2019-01", end_date: "2022-05" },
  ],
  projects: [{ name: "Pipeline linter", tech: ["Python"], bullets: ["Catches drift."] }],
  education: [{ degree: "MS", field: "Data Science", institution: "State U" }],
  skills: { programming: ["Python", "SQL"] },
  achievements: ["Team award."],
};

interface Saves {
  results: CanonicalProfile[];
  onSave: ReturnType<typeof vi.fn>;
}

// An editor that applies each mutation to a copy of the profile and records the result, or rejects.
function editor(fail: Error | null = null): Saves {
  const results: CanonicalProfile[] = [];
  const onSave = vi.fn(async (mutate: (draft: CanonicalProfile) => CanonicalProfile) => {
    if (fail !== null) throw fail;
    results.push(mutate(structuredClone(PROFILE)));
  });
  return { results, onSave };
}

function page(saves: Saves, props: { error?: string | null; saving?: boolean; clearError?: () => void; versionCount?: number; profile?: CanonicalProfile } = {}) {
  return render(
    <SectionedProfile
      profile={props.profile ?? PROFILE}
      versionCount={props.versionCount ?? 3}
      onSave={saves.onSave as never}
      saving={props.saving ?? false}
      error={props.error ?? null}
      clearError={props.clearError ?? (() => undefined)}
    />,
  );
}

const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const alerts = (root: ParentNode) => [...root.querySelectorAll('[role="alert"]')].map((alert) => text(alert));

beforeEach(() => {
  vi.stubGlobal("confirm", vi.fn(() => true));
});
afterEach(() => vi.unstubAllGlobals());

describe("what is drawn", () => {
  it("draws only the sections that have something in them, each under its own heading with a count", () => {
    const { container } = page(editor());
    const headings = [...container.querySelectorAll("section > .bj-section-heading h3")].map((h) => text(h));
    expect(headings).toEqual(["Experience", "Projects", "Education", "Skills", "Achievements"]);
    expect(text(query(container, "#section-experience .bj-section-count"))).toBe("2");
    expect(container.querySelector("#section-publications")).toBeNull();
    expect(container.querySelector("#section-patents")).toBeNull();
  });

  it("indexes the sections that are there, as real links to them, and not at all for a profile with one", () => {
    const { container } = page(editor());
    const links = [...query(container, 'nav[aria-label="Profile sections"]').querySelectorAll("a")].map((a) => [text(a), a.getAttribute("href")]);
    expect(links[0]).toEqual(["Experience", "#section-experience"]);
    expect(links).toHaveLength(5);
    const single = render(<SectionedProfile profile={{ personal: { name: "A" }, achievements: ["x"] }} versionCount={1} onSave={vi.fn() as never} saving={false} error={null} clearError={() => undefined} />);
    expect(single.container.querySelector("nav")).toBeNull();
  });

  it("offers to add a section only for the entry sections that are empty", () => {
    const { container } = page(editor());
    const added = [...query(container, '[aria-label="Add a section"]').querySelectorAll("button")].map((button) => text(button));
    expect(added).toEqual(["+ Add publication", "+ Add patent", "+ Add certification", "+ Add language", "+ Add volunteering"]);
  });

  it("says how many mandatory slots are used, and marks an entry that is mandatory", () => {
    const { container } = page(editor());
    expect(text(container)).toContain("1 of 6 mandatory slots used");
    expect(text(query(container, "#section-experience .bj-pin-badge"))).toBe("Mandatory · min 2");
  });

  it("names each Edit and Delete by the entry, with the visible words unchanged", () => {
    const { container } = page(editor());
    const edit = buttonNamed(container, "Edit Senior Data Engineer at Acme Analytics");
    expect(text(edit)).toBe("Edit");
    expect(container.querySelector('[aria-label="Delete Data Analyst at Orbit"]')).not.toBeNull();
    expect(container.querySelector('[aria-label="Edit MS, Data Science, State U"]')).not.toBeNull();
  });

  it("opens details as a disclosure that says whether it is open", () => {
    const { container } = page(editor());
    const toggle = buttonNamed(container, "Show details (1)");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    click(toggle);
    expect(buttonNamed(container, "Hide details").getAttribute("aria-expanded")).toBe("true");
    expect(text(container)).toContain("Cut refresh time to 90 minutes.");
  });

  it("has no serious accessibility violation", async () => {
    const { container } = page(editor());
    await expectNoSeriousA11yViolations(container);
  });
});

describe("editing an entry makes the next version", () => {
  it("opens the dialog with the entry, says what saving does, and saves the edited entry in place", async () => {
    const saves = editor();
    const { container } = page(saves, { versionCount: 3 });
    click(buttonNamed(container, "Edit Data Analyst at Orbit"));
    expect(text(dialog() as HTMLElement)).toContain("Saving creates version 4");
    const title = query<HTMLInputElement>(dialog() as HTMLElement, 'input[type="text"]');
    expect(title.value).toBe("Data Analyst");
    typeInto(title, "Senior Data Analyst");
    click(buttonNamed(dialog() as HTMLElement, "Save"));
    await settle();
    expect(saves.results[0].experience?.[1]).toMatchObject({ title: "Senior Data Analyst", company: "Orbit" });
    expect(saves.results[0].experience).toHaveLength(2);
    expect(dialog()).toBeNull();
  });

  it("adds a new entry at the end of its section", async () => {
    const saves = editor();
    const { container } = page(saves);
    click(buttonNamed(container, "+ Add publication"));
    expect(text(query(dialog() as HTMLElement, "h2"))).toBe("Add publication");
    typeInto(query(dialog() as HTMLElement, 'input[type="text"]'), "Schema drift");
    click(buttonNamed(dialog() as HTMLElement, "Save"));
    await settle();
    expect(saves.results[0].publications).toEqual([{ title: "Schema drift" }]);
  });

  it("keeps the dialog open, with the editor's error inside it, when the save is refused", async () => {
    const saves = editor(new Error("refused"));
    const { container } = page(saves, { error: "Your profile must keep at least one piece of evidence." });
    click(buttonNamed(container, "Edit Data Analyst at Orbit"));
    click(buttonNamed(dialog() as HTMLElement, "Save"));
    await settle();
    expect(dialog()).not.toBeNull();
    expect(alerts(dialog() as HTMLElement)).toEqual(["Your profile must keep at least one piece of evidence."]);
  });

  it("clears the editor's old error each time a dialog opens", () => {
    const clearError = vi.fn();
    const { container } = page(editor(), { clearError });
    click(buttonNamed(container, "Edit Data Analyst at Orbit"));
    expect(clearError).toHaveBeenCalledTimes(1);
  });

  it("counts the mandatory slots without the entry being edited, so its own pin does not use one up", () => {
    const { container } = page(editor());
    click(buttonNamed(container, "Edit Senior Data Engineer at Acme Analytics"));
    expect(text(dialog() as HTMLElement)).not.toContain("You've used all");
    expect(query<HTMLInputElement>(dialog() as HTMLElement, ".bj-pin-field input[type='checkbox']").checked).toBe(true);
  });

  it("closes with Esc and gives focus back to the Edit button that opened it", () => {
    const { container } = page(editor());
    const edit = buttonNamed(container, "Edit Data Analyst at Orbit");
    edit.focus();
    click(edit);
    keydown("Escape");
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(edit);
  });
});

describe("where focus goes when the dialog closes", () => {
  it("is the button that opened it, even when the press never focused it (Safari and Firefox on a Mac)", () => {
    const { container } = page(editor());
    const edit = buttonNamed(container, "Edit Data Analyst at Orbit");
    expect(document.activeElement).toBe(document.body);
    click(edit);
    keydown("Escape");
    expect(document.activeElement).toBe(edit);
  });

  it("is the button that added the entry, for an Add", () => {
    const { container } = page(editor());
    const add = buttonNamed(container, "+ Add publication");
    click(add);
    click(buttonNamed(dialog() as HTMLElement, "Cancel"));
    expect(document.activeElement).toBe(add);
  });

  it("is the heading of the section when a saved edit replaced the button that opened it", async () => {
    // The entry is keyed by its company, so renaming it replaces its Edit button.
    const saves = editor();
    const view = page(saves);
    click(buttonNamed(view.container, "Edit Data Analyst at Orbit"));
    const company = [...(dialog() as HTMLElement).querySelectorAll<HTMLInputElement>('input[type="text"]')][1];
    typeInto(company, "Orbit Retail");
    view.rerender(
      <SectionedProfile
        profile={{ ...PROFILE, experience: [PROFILE.experience![0], { ...PROFILE.experience![1], company: "Orbit Retail" }] }}
        versionCount={4}
        onSave={saves.onSave as never}
        saving={false}
        error={null}
        clearError={() => undefined}
      />,
    );
    keydown("Escape");
    expect(document.activeElement).toBe(query(view.container, "#section-experience-title"));
  });
});

describe("removing an entry", () => {
  it("asks first, and a refusal removes nothing", async () => {
    vi.stubGlobal("confirm", vi.fn(() => false));
    const saves = editor();
    const { container } = page(saves);
    click(buttonNamed(container, "Delete Data Analyst at Orbit"));
    await settle();
    expect(saves.onSave).not.toHaveBeenCalled();
    expect(vi.mocked(window.confirm).mock.calls[0][0]).toBe("Remove this experience? This creates a new profile version.");
  });

  it("removes exactly that entry as a new version", async () => {
    const saves = editor();
    const { container } = page(saves);
    click(buttonNamed(container, "Delete Data Analyst at Orbit"));
    await settle();
    expect(saves.results[0].experience?.map((entry) => entry.company)).toEqual(["Acme Analytics"]);
  });

  it("shows a refused removal as an alert above the sections, which can be dismissed", async () => {
    const saves = editor(new Error("Server said no."));
    const { container } = page(saves);
    click(buttonNamed(container, "Delete Data Analyst at Orbit"));
    await settle();
    expect(alerts(container)).toHaveLength(1);
    expect(alerts(container)[0]).toContain("That change was not saved: Save failed -- please try again.");
    click(buttonNamed(query(container, '[role="alert"]'), "Dismiss"));
    expect(alerts(container)).toEqual([]);
  });
});

describe("skills", () => {
  it("adds a skill to its category with Enter, and clears the box once it is saved", async () => {
    const saves = editor();
    const { container } = page(saves);
    const box = query<HTMLInputElement>(container, 'input[aria-label="Add to Programming"]');
    typeInto(box, "Rust");
    keydown("Enter", { target: box });
    await settle();
    expect(saves.results[0].skills?.programming).toEqual(["Python", "SQL", "Rust"]);
    expect(box.value).toBe("");
  });

  it("does not add a skill that is already there twice", async () => {
    const saves = editor();
    const { container } = page(saves);
    const box = query<HTMLInputElement>(container, 'input[aria-label="Add to Programming"]');
    typeInto(box, "Python");
    keydown("Enter", { target: box });
    await settle();
    expect(saves.results[0].skills?.programming).toEqual(["Python", "SQL"]);
  });

  it("removes a skill, naming it and its category", async () => {
    const saves = editor();
    const { container } = page(saves);
    click(buttonNamed(container, "Remove SQL from Programming"));
    await settle();
    expect(saves.results[0].skills?.programming).toEqual(["Python"]);
  });

  it("shows a refused skill as an alert, and keeps what was typed so it can be sent again", async () => {
    const saves = editor(new Error("nope"));
    const { container } = page(saves);
    const box = query<HTMLInputElement>(container, 'input[aria-label="Add to Programming"]');
    typeInto(box, "Rust");
    keydown("Enter", { target: box });
    await settle();
    expect(alerts(container)[0]).toContain("That change was not saved");
    expect(box.value).toBe("Rust");
  });

  it("shows a refused removal of a skill as an alert too", async () => {
    const saves = editor(new Error("nope"));
    const { container } = page(saves);
    click(buttonNamed(container, "Remove SQL from Programming"));
    await settle();
    expect(alerts(container)[0]).toContain("That change was not saved");
  });
});

describe("achievements", () => {
  it("adds one, edits one in place, and removes one", async () => {
    const saves = editor();
    const { container } = page(saves);
    const add = query<HTMLInputElement>(container, 'input[aria-label="Add an achievement"]');
    typeInto(add, "Open-source maintainer.");
    click(buttonNamed(container, "Add"));
    await settle();
    expect(saves.results[0].achievements).toEqual(["Team award.", "Open-source maintainer."]);

    click(buttonNamed(container, "Edit achievement 1"));
    const field = query<HTMLInputElement>(container, 'input[aria-label="Achievement 1"]');
    typeInto(field, "Team award, 2024.");
    click(buttonNamed(container, "Save"));
    await settle();
    expect(saves.results[1].achievements).toEqual(["Team award, 2024."]);

    click(buttonNamed(container, "Delete achievement 1"));
    await settle();
    expect(saves.results[2].achievements).toEqual([]);
  });

  it("shows a refused achievement as an alert and leaves the edit open with its text", async () => {
    const saves = editor(new Error("nope"));
    const { container } = page(saves);
    click(buttonNamed(container, "Edit achievement 1"));
    const field = query<HTMLInputElement>(container, 'input[aria-label="Achievement 1"]');
    typeInto(field, "Changed");
    click(buttonNamed(container, "Save"));
    await settle();
    expect(alerts(container)[0]).toContain("That change was not saved");
    expect(query<HTMLInputElement>(container, 'input[aria-label="Achievement 1"]').value).toBe("Changed");
  });

  it("shows a refused new achievement as an alert and keeps what was typed", async () => {
    const saves = editor(new Error("nope"));
    const { container } = page(saves);
    const add = query<HTMLInputElement>(container, 'input[aria-label="Add an achievement"]');
    typeInto(add, "Something");
    keydown("Enter", { target: add });
    await settle();
    expect(alerts(container)[0]).toContain("That change was not saved");
    expect(add.value).toBe("Something");
  });
});

describe("one failure is shown in one place", () => {
  it("does not draw the shared editor's error outside the dialog: a Personal details failure is not this block's", () => {
    const { container } = page(editor(), { error: "Work authorization could not be saved." });
    expect(alerts(container)).toEqual([]);
    expect(text(container)).not.toContain("Work authorization could not be saved.");
  });

  it("says a new version is being saved while one is, outside the dialog", () => {
    const { container } = page(editor(), { saving: true });
    expect(text(query(container, '[role="status"]'))).toBe("Saving a new version...");
  });
});
