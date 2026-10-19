// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, calls, callsTo, on, reset } from "../testing/fakeApi";
import { choose, settle, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { HeaderComposer } from "./HeaderComposer";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

// The header composer: it previews through the engine and saves the layout it was given, and every
// reordering that a drag can do, a button can do too.

const DOCUMENT = {
  id: "doc-1",
  user_id: "u",
  application_id: null,
  profile_version_id: "version-old",
  job_snapshot_id: null,
  section_order: [],
  section_visibility: {},
  header_layout: {
    chips: [
      { field: "phone", display_mode: "full" },
      { field: "email", display_mode: "full" },
      { field: "github", display_mode: "full" },
    ],
    separator: "pipe",
  },
  shape_overrides: {},
  assertions: [],
};

const CHIPS = [
  { field: "phone", text: "+1 555 0100", href: null },
  { field: "email", text: "asha@example.test", href: "mailto:asha@example.test" },
];

function describeDocument(overrides: Record<string, unknown> = {}) {
  on("GET /resume-documents/mine", { ...DOCUMENT, ...overrides });
  on("POST /resume-documents/doc-1/header/preview", { chips: CHIPS });
  on("PATCH /resume-documents/doc-1/header", {});
}

const page = (ui = <HeaderComposer />) => render(<MemoryRouter>{ui}</MemoryRouter>);
const rows = (root: ParentNode) => [...root.querySelectorAll("li.bj-header-composer-row")];
const names = (root: ParentNode) => rows(root).map((row) => text(row.querySelector(".bj-header-composer-field") as Element));

beforeEach(() => reset());

describe("the header composer", () => {
  it("shows a placeholder, named for a screen reader, while it reads", async () => {
    on("GET /resume-documents/mine", () => new Promise(() => undefined));
    const { container } = page();
    await settle(1);
    expect(text(container)).toContain("Loading header...");
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });

  it("previews the chips the engine resolved, in a labelled group, and lists the fields in order", async () => {
    describeDocument();
    const { container } = page();
    await settle();
    const preview = query(container, '[aria-label="Header preview"]');
    expect([...preview.children].map((chip) => text(chip))).toEqual(["+1 555 0100", "asha@example.test"]);
    expect(query<HTMLAnchorElement>(preview, "a").getAttribute("href")).toBe("mailto:asha@example.test");
    expect(names(container)).toEqual(["Phone", "Email", "GitHub"]);
    expect(callsTo("POST /resume-documents/doc-1/header/preview")[0].body).toEqual({ header_layout: DOCUMENT.header_layout });
  });

  it("reads the master document, or the application's when it is given one", async () => {
    describeDocument();
    page();
    await settle();
    expect(callsTo("GET /resume-documents/mine")).toHaveLength(1);
    reset();
    on("GET /resume-documents/mine?application_id=app-9", DOCUMENT);
    on("POST /resume-documents/doc-1/header/preview", { chips: CHIPS });
    page(<HeaderComposer applicationId="app-9" />);
    await settle();
    expect(callsTo("GET /resume-documents/mine?application_id=app-9")).toHaveLength(1);
  });

  it("moves a field down with its button: previews, then saves the new order", async () => {
    describeDocument();
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Move Phone down"));
    await settle();
    const order = (body: unknown) => (body as { header_layout: { chips: { field: string }[] } }).header_layout.chips.map((chip) => chip.field);
    expect(order(callsTo("PATCH /resume-documents/doc-1/header")[0].body)).toEqual(["email", "phone", "github"]);
    expect(names(container)).toEqual(["Email", "Phone", "GitHub"]);
    expect(calls.map((call) => `${call.method} ${call.path}`).slice(-2)).toEqual([
      "POST /resume-documents/doc-1/header/preview",
      "PATCH /resume-documents/doc-1/header",
    ]);
  });

  it("moves a field up, and leaves the first one's Up and the last one's Down off", async () => {
    describeDocument();
    const { container } = page();
    await settle();
    expect(buttonNamed(container, "Move Phone up").disabled).toBe(true);
    expect(buttonNamed(container, "Move GitHub down").disabled).toBe(true);
    click(buttonNamed(container, "Move GitHub up"));
    await settle();
    expect(names(container)).toEqual(["Phone", "GitHub", "Email"]);
  });

  it("keeps a moved field's own button focused, so a keyboard user can keep moving it", async () => {
    describeDocument();
    const { container } = page();
    await settle();
    const down = buttonNamed(container, "Move Phone down");
    down.focus();
    click(down);
    await settle();
    expect(rows(container)[1].contains(document.activeElement)).toBe(true);
  });

  it("removes a field and offers to add it back, and adds one", async () => {
    describeDocument();
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Remove Email"));
    await settle();
    expect(names(container)).toEqual(["Phone", "GitHub"]);
    expect(buttonNamed(container, "+ Email")).toBeDefined();
    click(buttonNamed(container, "+ LinkedIn"));
    await settle();
    expect(names(container)).toEqual(["Phone", "GitHub", "LinkedIn"]);
  });

  it("changes how a field is shown, and warns that a label hides the address on paper", async () => {
    describeDocument();
    const { container } = page();
    await settle();
    choose(query<HTMLSelectElement>(container, 'select[aria-label="How GitHub is shown"]'), "label");
    await settle();
    const saved = callsTo("PATCH /resume-documents/doc-1/header")[0].body as { header_layout: { chips: { field: string; display_mode: string }[] } };
    expect(saved.header_layout.chips.find((chip) => chip.field === "github")?.display_mode).toBe("label");
    expect(text(container)).toContain("a label-mode field shows its link text only on screen -- the URL is invisible on paper.");
  });

  it("says Saving... in a polite status while a change is on its way", async () => {
    describeDocument();
    on("PATCH /resume-documents/doc-1/header", () => new Promise(() => undefined));
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Remove Email"));
    await settle();
    const status = query(container, '[role="status"]');
    expect(text(status)).toBe("Saving...");
  });
});

describe("a preview that is out of date", () => {
  it("says it uses an earlier version of the profile only when the ids are known and differ", async () => {
    describeDocument();
    const stale = page(<HeaderComposer activeVersionId="version-new" />);
    await settle();
    expect(text(stale.container)).toContain("This preview uses an earlier version of your profile.");
    stale.unmount();
    const same = page(<HeaderComposer activeVersionId="version-old" />);
    await settle();
    expect(text(same.container)).not.toContain("earlier version");
    same.unmount();
    const unknown = page(<HeaderComposer />);
    await settle();
    expect(text(unknown.container)).not.toContain("earlier version");
  });
});

describe("its trail line", () => {
  it("is drawn only when asked for, so an embedder that draws its own is unchanged", async () => {
    describeDocument();
    const plain = page();
    await settle();
    expect(plain.container.querySelector(".bj-trail")).toBeNull();
    plain.unmount();
    const trailed = page(<HeaderComposer showTrail />);
    await settle();
    const trail = query(trailed.container, ".bj-trail");
    expect(text(trail)).toContain("Chips resolved by code · from your profile, no model involved");
    // the mark is plain code, never a model's star, and is hidden from a screen reader
    expect(query(trail, ".bj-trail-glyph--muted").getAttribute("aria-hidden")).toBe("true");
    expect(trail.querySelector(".bj-trail-glyph--violet")).toBeNull();
  });

  it("opens a receipt built from the document's returned fields: the profile version it was made from, and the chip count", async () => {
    describeDocument();
    const { container } = page(<HeaderComposer showTrail />);
    await settle();
    click(query(container, "button.bj-trail-trigger"));
    const receipt = text(query(container, ".bj-receipt"));
    expect(receipt).toContain("profile version");
    expect(receipt).toContain("version-");
    expect(receipt).toContain("chips");
  });
});

describe("when it cannot read or save", () => {
  it("shows a failed read as an alert with Retry, which reads again, and a red-cross trail line", async () => {
    on("GET /resume-documents/mine", new FakeApiError("The resume service is not answering", "INTERNAL_ERROR", undefined, 500));
    const { container } = page(<HeaderComposer showTrail />);
    await settle();
    const alert = query(container, '[role="alert"]');
    expect(text(alert)).toContain("The resume service is not answering");
    expect(text(query(container, ".bj-trail"))).toContain("The resume service is not answering");
    expect(query(container, ".bj-trail .bj-trail-glyph--danger").getAttribute("aria-hidden")).toBe("true");
    describeDocument();
    click(buttonNamed(alert, "Retry"));
    await settle();
    expect(names(container)).toEqual(["Phone", "Email", "GitHub"]);
    expect(callsTo("GET /resume-documents/mine")).toHaveLength(2);
  });

  it("shows a failed save the same way, and Retry re-reads the card rather than re-sending the change", async () => {
    describeDocument();
    on("PATCH /resume-documents/doc-1/header", new FakeApiError("Could not save", "INTERNAL_ERROR", undefined, 500));
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Remove Email"));
    await settle();
    expect(text(query(container, '[role="alert"]'))).toContain("Could not save");
    on("PATCH /resume-documents/doc-1/header", {});
    click(buttonNamed(container, "Retry"));
    await settle();
    expect(callsTo("PATCH /resume-documents/doc-1/header")).toHaveLength(1);
    expect(names(container)).toEqual(["Phone", "Email", "GitHub"]);
  });

  it("shows no profile yet as the setup notice with its link, and no Retry", async () => {
    on("GET /resume-documents/mine", new FakeApiError("Import a profile first.", "SETUP_REQUIRED", undefined, 409));
    const { container } = page();
    await settle();
    expect(text(container)).toContain("Import a profile first.");
    expect([...container.querySelectorAll("button")].some((button) => text(button) === "Retry")).toBe(false);
  });
});

describe("accessibility", () => {
  it("has no serious violation, and names every control", async () => {
    describeDocument();
    const { container } = page(<HeaderComposer showTrail />);
    await settle();
    await expectNoSeriousA11yViolations(container);
    for (const label of ["Move Phone up", "Move Phone down", "Remove Phone", "How Phone is shown"]) {
      expect(container.querySelector(`[aria-label="${label}"]`), label).not.toBeNull();
    }
  });
});
