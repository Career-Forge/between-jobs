// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CanonicalProfile } from "../lib/profileTypes";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { SectionOrderEditor } from "./SectionOrderEditor";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

// Section order and visibility: the sections the generated resume has, and in what order. A drag and a
// pair of buttons do the same thing, and a section with nothing in it cannot be switched on.

const PROFILE: CanonicalProfile = {
  personal: { name: "Asha Verma" },
  summary_bullets: ["Builds reliable pipelines."],
  experience: [{ title: "Engineer", company: "Acme", start_date: "2022-01", end_date: "present" }],
  education: [{ degree: "MS", institution: "State U" }],
};

const DOCUMENT = {
  id: "doc-1",
  user_id: "u",
  application_id: null,
  profile_version_id: "v1",
  job_snapshot_id: null,
  section_order: ["summary", "experience", "education"],
  section_visibility: {},
  header_layout: {},
  shape_overrides: {},
  assertions: [],
};

const rows = (root: ParentNode) => [...root.querySelectorAll("li.bj-header-composer-row")];
const labels = (root: ParentNode) => rows(root).map((row) => text(row.querySelector(".bj-header-composer-field") as Element));

beforeEach(() => {
  reset();
  on("GET /resume-documents/mine", DOCUMENT);
  on("PATCH /resume-documents/doc-1/sections", {});
});

describe("the section order editor", () => {
  it("shows a placeholder, named for a screen reader, while it reads", async () => {
    on("GET /resume-documents/mine", () => new Promise(() => undefined));
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle(1);
    expect(text(container)).toContain("Loading sections...");
  });

  it("lists the saved order, then every section the saved order lacked, none dropped", async () => {
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    expect(labels(container)).toEqual(["Summary", "Experience", "Education", "Projects", "Skills", "Achievements", "Certifications", "Publications", "Patents"]);
  });

  it("shows a section with content as on, one without as 'No content yet' and unable to be switched on", async () => {
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    expect(query<HTMLInputElement>(container, 'input[aria-label="Show Experience"]').checked).toBe(true);
    const patents = query<HTMLInputElement>(container, 'input[aria-label="Show Patents"]');
    expect(patents.checked).toBe(false);
    expect(patents.disabled).toBe(true);
    expect(text(patents.closest("li") as Element)).toContain("No content yet");
  });

  it("moves a section down with its button and saves the order with the visibility it had", async () => {
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    click(buttonNamed(container, "Move Summary down"));
    await settle();
    const saved = callsTo("PATCH /resume-documents/doc-1/sections")[0].body as { section_order: string[]; section_visibility: Record<string, boolean> };
    expect(saved.section_order.slice(0, 3)).toEqual(["experience", "summary", "education"]);
    expect(saved.section_visibility).toMatchObject({ summary: true, experience: true, education: true, patents: false });
    expect(labels(container).slice(0, 3)).toEqual(["Experience", "Summary", "Education"]);
  });

  it("moves a section up, and leaves the first one's Up and the last one's Down off", async () => {
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    expect(buttonNamed(container, "Move Summary up").disabled).toBe(true);
    expect(buttonNamed(container, "Move Patents down").disabled).toBe(true);
    click(buttonNamed(container, "Move Education up"));
    await settle();
    expect(labels(container).slice(0, 3)).toEqual(["Summary", "Education", "Experience"]);
  });

  it("hides a section, says Hidden in words, and saves it", async () => {
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    click(query(container, 'input[aria-label="Show Education"]'));
    await settle();
    expect((callsTo("PATCH /resume-documents/doc-1/sections")[0].body as { section_visibility: Record<string, boolean> }).section_visibility.education).toBe(false);
    expect(text(query(container, 'input[aria-label="Show Education"]').closest("li") as Element)).toContain("Hidden");
  });

  it("reads the master document, or the application's when it is given one", async () => {
    render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    expect(callsTo("GET /resume-documents/mine")).toHaveLength(1);
    reset();
    on("GET /resume-documents/mine?application_id=app-9", DOCUMENT);
    render(<SectionOrderEditor profile={PROFILE} applicationId="app-9" />);
    await settle();
    expect(callsTo("GET /resume-documents/mine?application_id=app-9")).toHaveLength(1);
  });

  it("says Saving... in a polite status while a change is on its way", async () => {
    on("PATCH /resume-documents/doc-1/sections", () => new Promise(() => undefined));
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    click(buttonNamed(container, "Move Summary down"));
    await settle();
    expect(text(query(container, '[role="status"]'))).toBe("Saving...");
  });
});

describe("when it cannot read or save", () => {
  it("shows a failed read as an alert with Retry, which reads again", async () => {
    on("GET /resume-documents/mine", new FakeApiError("The resume service is not answering", "INTERNAL_ERROR", undefined, 500));
    const { container } = render(<SectionOrderEditor profile={PROFILE} showTrail />);
    await settle();
    const alert = query(container, '[role="alert"]');
    expect(text(alert)).toContain("The resume service is not answering");
    expect(text(query(container, ".bj-trail"))).toContain("The resume service is not answering");
    on("GET /resume-documents/mine", DOCUMENT);
    click(buttonNamed(alert, "Retry"));
    await settle();
    expect(labels(container)[0]).toBe("Summary");
  });

  it("shows a failed save the same way, and Retry re-reads the card", async () => {
    on("PATCH /resume-documents/doc-1/sections", new FakeApiError("Could not save", "INTERNAL_ERROR", undefined, 500));
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    click(buttonNamed(container, "Move Summary down"));
    await settle();
    expect(text(query(container, '[role="alert"]'))).toContain("Could not save");
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(labels(container)[0]).toBe("Summary");
  });

  it("draws no trail line unless asked, so an embedder that draws its own is unchanged", async () => {
    on("GET /resume-documents/mine", new FakeApiError("The resume service is not answering", "INTERNAL_ERROR", undefined, 500));
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    expect(container.querySelector(".bj-trail")).toBeNull();
  });
});

describe("accessibility", () => {
  it("has no serious violation, an ordered list that names itself, and a name on every control", async () => {
    const { container } = render(<SectionOrderEditor profile={PROFILE} />);
    await settle();
    await expectNoSeriousA11yViolations(container);
    expect(query(container, "ul").getAttribute("aria-label")).toBe("Resume sections, in order");
    for (const label of ["Move Summary up", "Move Summary down", "Show Summary"]) {
      expect(container.querySelector(`[aria-label="${label}"]`), label).not.toBeNull();
    }
  });
});
