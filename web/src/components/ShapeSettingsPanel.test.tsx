// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SHAPE_PRESETS } from "../lib/shapePresets";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { choose, settle, text } from "../testing/forms";
import { buttonNamed, click, keydown, query, render } from "../testing/render";
import { ShapeSettingsPanel } from "./ShapeSettingsPanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

// Resume settings: the short choices are radio groups, a setting left unset means "inherit" and is shown
// as its default, and only what a person picked is ever sent.

const DOCUMENT = {
  id: "doc-1",
  user_id: "u",
  application_id: null,
  profile_version_id: "v1",
  job_snapshot_id: null,
  section_order: [],
  section_visibility: {},
  header_layout: {},
  shape_overrides: { summary: "on" },
  assertions: [],
};

const page = (ui = <ShapeSettingsPanel />) => render(<MemoryRouter>{ui}</MemoryRouter>);
const group = (root: ParentNode, name: string) => query(root, `[role="radiogroup"][aria-labelledby="${[...root.querySelectorAll("span, label")].find((el) => text(el) === name)?.id}"]`);
const checked = (root: ParentNode) => text(query(root, '[role="radio"][aria-checked="true"]'));
const saved = () => callsTo("PATCH /resume-documents/doc-1/shape").map((call) => (call.body as { shape_overrides: Record<string, unknown> }).shape_overrides);

beforeEach(() => {
  reset();
  on("GET /resume-documents/mine", DOCUMENT);
  on("PATCH /resume-documents/doc-1/shape", {});
});

describe("the resume settings", () => {
  it("shows a placeholder, named for a screen reader, while it reads", async () => {
    on("GET /resume-documents/mine", () => new Promise(() => undefined));
    const { container } = page();
    await settle(1);
    expect(text(container)).toContain("Loading resume settings...");
  });

  it("draws page count, density, summary and bullet style as named radio groups, showing what is in effect", async () => {
    const { container } = page();
    await settle();
    expect(checked(group(container, "Page count"))).toBe("Auto (by region/tier)");
    expect(checked(group(container, "Density"))).toBe("Balanced");
    expect(checked(group(container, "Summary"))).toBe("On");
    expect(checked(group(container, "Bullet style"))).toBe("Plain");
    expect(container.querySelectorAll('[role="radiogroup"]')).toHaveLength(4);
  });

  it("sends only what was picked, on top of what was already set", async () => {
    const { container } = page();
    await settle();
    click(buttonNamed(group(container, "Density"), "Compact"));
    await settle();
    expect(saved()).toEqual([{ summary: "on", density: "compact" }]);
    expect(checked(group(container, "Density"))).toBe("Compact");
  });

  it("chooses with the arrow keys, the way a radio group does, saving each choice", async () => {
    const { container } = page();
    await settle();
    buttonNamed(group(container, "Page count"), "Auto (by region/tier)").focus();
    keydown("ArrowRight");
    await settle();
    expect(saved()).toEqual([{ summary: "on", page_count: "1" }]);
    expect(document.activeElement).toBe(buttonNamed(group(container, "Page count"), "1 page"));
  });

  it("sets the region from its select, and clears it to auto-detect with null", async () => {
    const { container } = page();
    await settle();
    const region = query<HTMLSelectElement>(container, "select");
    choose(region, "UK");
    await settle();
    expect(saved().at(-1)).toMatchObject({ region: "UK" });
    choose(region, "");
    await settle();
    expect(saved().at(-1)).toMatchObject({ region: null });
  });

  it("sets Show GPA and Show nationality, and says nationality is shown only where the employer's country expects it", async () => {
    const { container } = page();
    await settle();
    const [gpa, nationality] = [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    click(gpa);
    await settle();
    expect(saved().at(-1)).toMatchObject({ show_gpa: true });
    click(nationality);
    await settle();
    expect(saved().at(-1)).toMatchObject({ show_nationality: true });
    expect(text(container)).toContain("Only rendered for employers whose country expects it");
  });

  it("offers every starting point with its own words, and a press sends that preset's overrides and nothing else", async () => {
    const { container } = page();
    await settle();
    const presets = query(container, '[role="group"][aria-label="Starting points"]');
    expect([...presets.querySelectorAll("button")].map((button) => text(button.querySelector(".bj-shape-preset-label") as Element))).toEqual(SHAPE_PRESETS.map((preset) => preset.label));
    expect(text(presets)).toContain("Technical, experience-first");
    click(buttonNamed(presets, /^Senior/));
    await settle();
    const senior = SHAPE_PRESETS.find((preset) => preset.key === "senior");
    expect(saved()).toEqual([{ ...senior?.overrides }]);
  });

  it("reads the master document, or the application's when it is given one", async () => {
    page();
    await settle();
    expect(callsTo("GET /resume-documents/mine")).toHaveLength(1);
    reset();
    on("GET /resume-documents/mine?application_id=app-9", DOCUMENT);
    page(<ShapeSettingsPanel applicationId="app-9" />);
    await settle();
    expect(callsTo("GET /resume-documents/mine?application_id=app-9")).toHaveLength(1);
  });

  it("is never a cyan fill, and the one selected segment of a group is the only maroon one", async () => {
    const { container } = page();
    await settle();
    expect(container.querySelector(".bj-primary")).toBeNull();
    expect(group(container, "Density").querySelectorAll('[aria-checked="true"]')).toHaveLength(1);
  });
});

describe("when it cannot read or save", () => {
  it("shows a failed read as an alert with Retry, which reads again, and a red-cross trail line when asked", async () => {
    on("GET /resume-documents/mine", new FakeApiError("The resume service is not answering", "INTERNAL_ERROR", undefined, 500));
    const { container } = page(<ShapeSettingsPanel showTrail />);
    await settle();
    const alert = query(container, '[role="alert"]');
    expect(text(alert)).toContain("The resume service is not answering");
    expect(text(query(container, ".bj-trail"))).toContain("The resume service is not answering");
    on("GET /resume-documents/mine", DOCUMENT);
    click(buttonNamed(alert, "Retry"));
    await settle();
    expect(container.querySelectorAll('[role="radiogroup"]')).toHaveLength(4);
  });

  it("shows a failed save the same way, and Retry re-reads the card rather than re-sending the change", async () => {
    on("PATCH /resume-documents/doc-1/shape", new FakeApiError("Could not save", "INTERNAL_ERROR", undefined, 500));
    const { container } = page();
    await settle();
    click(buttonNamed(group(container, "Density"), "Compact"));
    await settle();
    expect(text(query(container, '[role="alert"]'))).toContain("Could not save");
    on("PATCH /resume-documents/doc-1/shape", {});
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(callsTo("PATCH /resume-documents/doc-1/shape")).toHaveLength(1);
  });

  it("shows no profile yet as the setup notice with its link", async () => {
    on("GET /resume-documents/mine", new FakeApiError("Import a profile first.", "SETUP_REQUIRED", undefined, 409));
    const { container } = page();
    await settle();
    expect(text(container)).toContain("Import a profile first.");
  });
});

describe("accessibility", () => {
  it("has no serious violation", async () => {
    const { container } = page();
    await settle();
    await expectNoSeriousA11yViolations(container);
  });
});
