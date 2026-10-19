// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseImportResponse } from "../lib/resumeImportDraft";
import { buildReview, fieldsToCheck } from "../lib/resumeImportReview";
import { findForbiddenWord } from "../lib/trail";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text, typeInto } from "../testing/forms";
import { buttonNamed, click, installViewport, keydown, query, render } from "../testing/render";
import Profile from "./Profile";

// The Profile page, rendered whole with its requests answered by the test: every state it can be in, the
// band's rows on it, the flows that change the profile (the JSON import, a resume file's draft, an edit),
// and the three placements of its trail lines.

const upload = vi.hoisted(() => ({ fetchBytes: vi.fn() }));
vi.mock("../lib/api", async () => ({ ...(await import("../testing/fakeApi")).apiModule, apiFetchBytes: upload.fetchBytes }));
vi.mock("../auth", () => ({ useAuth: () => ({ signOut: vi.fn() }) }));

const VERSION_ID = "3f2a9c1e-aaaa-bbbb-cccc-1234567890ab";

function profile(overrides: Record<string, unknown> = {}) {
  return {
    id: VERSION_ID,
    canonical_json: {
      personal: { name: "Asha Verma", headline: "Data engineer", links: { scholar: "scholar.example.test/asha" }, work_authorization: "US citizen." },
      experience: [{ title: "Senior Data Engineer", company: "Acme Analytics", start_date: "2022-06", end_date: "present", is_current: true, bullets: ["Cut refresh time."] }],
      education: [{ degree: "MS", institution: "State U" }],
      skills: { programming: ["Python"] },
      achievements: ["Team award."],
    },
    activated_at: "2026-10-12T10:15:00Z",
    created_at: "2026-10-12T09:58:00Z",
    source_kind: "web_json_paste",
    version_count: 4,
    ...overrides,
  };
}

const READY_KEY = [{ id: "c1", service: "llm", provider: "openrouter", model: "vendor/model-a", is_validated: true, setup_state: "ready", updated_at: "2026-10-12T10:00:00Z" }];

const DOCUMENT = {
  id: "doc-1",
  user_id: "u",
  application_id: null,
  profile_version_id: VERSION_ID,
  job_snapshot_id: null,
  section_order: ["summary", "experience", "education"],
  section_visibility: {},
  header_layout: { chips: [{ field: "email", display_mode: "full" }], separator: "pipe" },
  shape_overrides: {},
  assertions: [],
};

function describeAccount({ current = profile() as unknown, credentials = READY_KEY as unknown, document = DOCUMENT as unknown } = {}) {
  on("GET /profile/current", current);
  on("GET /credentials", credentials);
  on("GET /resume-documents/mine", document);
  on("POST /resume-documents/doc-1/header/preview", { chips: [{ field: "email", text: "asha@example.test", href: null }] });
  on("PATCH /resume-documents/doc-1/header", {});
  on("PATCH /resume-documents/doc-1/sections", {});
  on("PATCH /resume-documents/doc-1/shape", {});
}

const NOT_FOUND = new FakeApiError("no active profile yet -- import one first", "NOT_FOUND", undefined, 404);

function page(path = "/profile") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Profile />
    </MemoryRouter>,
  );
}

const band = (root: ParentNode) => query(root, 'section[aria-label="Next step"]');
const cyan = (root: ParentNode) => [...root.querySelectorAll(".bj-primary, .bj-button-link-primary")];
const trails = (root: ParentNode) => [...root.querySelectorAll(".bj-trail")].map((line) => text(line));
const named = (root: ParentNode, label: string | RegExp) =>
  [...root.querySelectorAll<HTMLElement>("button, a")].find((element) => (typeof label === "string" ? text(element) === label : label.test(text(element)))) as HTMLElement;

beforeEach(() => {
  reset();
  upload.fetchBytes.mockReset();
  Element.prototype.scrollIntoView = () => undefined;
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-19T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ── the page and its states ────────────────────────────────────────────────

describe("the page", () => {
  it("has one h1, Profile, a subtitle, and a way to Settings that is an outlined link", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect([...container.querySelectorAll("h1")].map((h) => text(h))).toEqual(["Profile"]);
    expect(text(container)).toContain("What every resume draft, score and practice question is built from. Read from your saved profile and resume settings.");
    const settings = named(container, "Settings") as HTMLAnchorElement;
    expect(settings.getAttribute("href")).toBe("/settings");
    expect(settings.classList.contains("bj-button-link-primary")).toBe(false);
  });

  it("is a document: the chapters and blocks of an active profile, in order, under one h1", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    const headings = [...container.querySelectorAll("h2, h3")].map((h) => text(h));
    expect(headings.slice(0, 7)).toEqual(["Asha Verma", "Personal details", "Resume layout", "Header", "Resume structure", "Resume settings", "Your profile"]);
    expect(headings).toContain("Experience");
    expect(headings).toContain("Import a new version");
    expect(headings).toContain("Import from a resume file (PDF or DOCX)");
    expect(headings.at(-1)).toBe("Delete my account");
  });
});

describe("loading", () => {
  it("shows the band's eyebrow and Loading... and nothing lit, with a placeholder in place of the body", async () => {
    on("GET /profile/current", () => new Promise(() => undefined));
    on("GET /credentials", () => new Promise(() => undefined));
    const { container } = page();
    await settle(1);
    expect(text(band(container))).toBe("Next stepLoading...");
    expect(cyan(container)).toHaveLength(0);
    expect(text(container)).toContain("Loading your profile...");
    expect(container.querySelector(".bj-doc")).toBeNull();
  });

  it("keeps the band loading while only the credentials are out, since the page asked for them", async () => {
    on("GET /profile/current", profile());
    on("GET /credentials", () => new Promise(() => undefined));
    on("GET /resume-documents/mine", DOCUMENT);
    on("POST /resume-documents/doc-1/header/preview", { chips: [] });
    const { container } = page();
    await settle();
    expect(text(band(container))).toBe("Next stepLoading...");
  });

  it("does not return to loading for a re-read after an edit: the page stays as it was", async () => {
    describeAccount();
    on("POST /profile/versions", { id: "v5" });
    on("POST /profile/versions/v5/activate", {});
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Edit Senior Data Engineer at Acme Analytics"));
    let during = "";
    on("GET /profile/current", () => {
      during = text(container);
      return profile({ version_count: 5 });
    });
    click(buttonNamed(query(document.body, '[role="dialog"]'), "Save"));
    await settle();
    expect(during).not.toContain("Loading your profile...");
    expect(during).toContain("Asha Verma");
  });
});

describe("no profile yet (the 404)", () => {
  it("lights the band with G2, pointing at the import section of this page, and that is the page's only cyan", async () => {
    describeAccount({ current: NOT_FOUND });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Everything here starts from your profile. Import it first.");
    expect(text(band(container))).toContain("why: no active profile");
    const link = named(band(container), "Set up your profile") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/profile#set-up");
    expect(cyan(container)).toEqual([link]);
  });

  it("teaches what will appear and what it feeds, with no example and no number", async () => {
    describeAccount({ current: NOT_FOUND });
    const { container } = page();
    await settle();
    const empty = query(container, ".bj-state--empty");
    expect(text(empty)).toContain("Your profile isn't set up yet");
    expect(text(empty)).toContain("every resume draft, score and practice question is built from it");
    expect(empty.querySelector("button, a")).toBeNull();
    expect(text(empty)).not.toMatch(/\d/);
  });

  it("draws the import section the band points at, with the file import above the JSON import", async () => {
    describeAccount({ current: NOT_FOUND });
    const { container } = page();
    await settle();
    const section = query(container, "#set-up");
    const headings = [...section.querySelectorAll("h2")].map((h) => text(h));
    expect(headings).toEqual(["Import from a resume file (PDF or DOCX)", "Set up your profile"]);
    expect(container.querySelector("#new-version")).toBeNull();
    expect(container.querySelector("#profile")).toBeNull();
  });

  it("draws no active-profile blocks and still the account card", async () => {
    describeAccount({ current: NOT_FOUND });
    const { container } = page();
    await settle();
    expect(container.querySelector(".bj-profile-identity")).toBeNull();
    expect(container.querySelector(".bj-account-delete")).not.toBeNull();
  });

  it("arriving at #set-up leaves keyboard focus on the first heading of the import section", async () => {
    describeAccount({ current: NOT_FOUND });
    const { container } = page("/profile#set-up");
    await settle();
    expect(document.activeElement).toBe(query(query(container, "#set-up"), "h2"));
  });
});

describe("an active profile with nothing waiting", () => {
  it("is unlit: PR0 says it is active and since when, from the activation date the API returned, and no cyan fill is drawn", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Your profile is active. Edits save a new version.");
    expect(text(band(container))).toContain("why: profile active since Oct 12");
    expect(band(container).querySelector("a, button")).toBeNull();
    expect(cyan(container)).toHaveLength(0);
  });

  it("leaves the date out of the why line when the activation was not returned", async () => {
    describeAccount({ current: profile({ activated_at: null }) });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("why: profile active");
    expect(text(band(container))).not.toContain("since");
  });

  it("shows the name, the headline, an Active mark that says it in words, the Scholar chip and when it was activated", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    const identity = query(container, ".bj-profile-identity");
    expect(text(query(identity, "h2"))).toBe("Asha Verma");
    expect(text(identity)).toContain("Data engineer");
    expect(text(query(identity, ".bj-badge-emerald"))).toContain("Active");
    expect(query<HTMLAnchorElement>(identity, "a.bj-contact-chip").getAttribute("href")).toBe("https://scholar.example.test/asha");
    expect(text(identity)).toContain("Activated");
  });

  it("omits the Scholar chip when there is none", async () => {
    describeAccount({ current: profile({ canonical_json: { personal: { name: "Asha Verma" }, achievements: ["x"] } }) });
    const { container } = page();
    await settle();
    expect(query(container, ".bj-profile-identity").querySelector(".bj-contact-chip")).toBeNull();
  });

  it("switches between Sections and Raw JSON, announced with aria-pressed, and exports and copies the JSON", async () => {
    describeAccount();
    const write = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText: write } });
    const { container } = page();
    await settle();
    const sections = buttonNamed(container, "Sections");
    const json = buttonNamed(container, "Raw JSON");
    expect([sections.getAttribute("aria-pressed"), json.getAttribute("aria-pressed")]).toEqual(["true", "false"]);
    expect(container.querySelector(".bj-json-view")).toBeNull();
    click(json);
    expect([sections.getAttribute("aria-pressed"), json.getAttribute("aria-pressed")]).toEqual(["false", "true"]);
    const view = query(container, ".bj-json-view");
    expect(view.getAttribute("role")).toBe("region");
    expect(view.getAttribute("aria-label")).toBe("Raw profile JSON");
    expect(view.tabIndex).toBe(0);
    click(buttonNamed(container, "Copy"));
    await settle();
    expect(write).toHaveBeenCalledWith(expect.stringContaining('"name": "Asha Verma"'));
    expect(buttonNamed(container, "Copied ✓")).toBeDefined();
    expect(container.querySelector('[aria-label="Header preview"]')).not.toBeNull();
  });

  it("says when the clipboard refused, rather than saying nothing", async () => {
    describeAccount();
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) } });
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Raw JSON"));
    click(buttonNamed(container, "Copy"));
    await settle();
    expect(text(container)).toContain("Couldn't copy to the clipboard.");
  });

  it("gives the header, the section order and the settings the active version, and asks each for its trail line", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect(trails(container).some((line) => line.includes("Chips resolved by code"))).toBe(true);
    expect(text(container)).not.toContain("earlier version of your profile");
  });
});

// ── the trail lines ────────────────────────────────────────────────────────

describe("the trail lines are made of returned fields only", () => {
  it("says how the version was made, when it was saved and how many versions there are", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    expect(trails(container)).toContain("● Saved from your JSON or edits · parsed by code · Oct 12 · 4 versions saved");
  });

  it("says a model read it for a resume file, and names no model", async () => {
    describeAccount({ current: profile({ source_kind: "web_pdf_import" }) });
    const { container } = page();
    await settle();
    const line = trails(container).find((candidate) => candidate.includes("Read by your model"));
    expect(line).toBe("✦ Read by your model · from your PDF file · Oct 12 · 4 versions saved");
    expect(line).not.toMatch(/gpt|claude|gemini|llama|deepseek|openrouter/i);
  });

  it("leaves every slot out that the API did not return, with its separator, and says nothing about who acted for a source it does not know", async () => {
    describeAccount({ current: profile({ source_kind: "telegram_json", created_at: undefined, version_count: undefined }) });
    const { container } = page();
    await settle();
    expect(trails(container)).toContain("Saved to your account");
  });

  it("opens a receipt of the returned fields, with a short id, never the whole row id", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    const opener = [...container.querySelectorAll<HTMLButtonElement>("button.bj-trail-trigger")].find((button) => text(button).includes("Saved from your JSON or edits"));
    click(opener as HTMLButtonElement);
    const receipt = text(query(container, ".bj-receipt"));
    expect(receipt).toContain("3f2a9c1e");
    expect(receipt).not.toContain(VERSION_ID);
    expect(receipt).toContain("pasted or uploaded JSON, or an edit saved from this page");
    expect(receipt).not.toContain("web_json_paste");
    expect(receipt).toContain("2026-10-12T10:15:00Z");
  });

  it("never says verified, and draws a trail on no form", async () => {
    describeAccount();
    const { container } = page();
    await settle();
    for (const line of trails(container)) expect(findForbiddenWord(line), line).toBeNull();
    expect(query(container, "#personal-details").querySelector(".bj-trail")).toBeNull();
  });
});

// ── where the trail lines sit ──────────────────────────────────────────────

describe("the trail margin and its inline and sheet forms", () => {
  it("from 1280px the trail lines sit in a margin beside their blocks, and the page lifts nothing into a summary", async () => {
    installViewport(1400);
    describeAccount();
    const { container } = page();
    await settle();
    const layout = query(container, ".bj-doc");
    expect(layout.classList.contains("bj-doc--margin")).toBe(true);
    const inMargin = [...layout.querySelectorAll(".bj-doc-margin .bj-trail")];
    expect(inMargin.length).toBeGreaterThanOrEqual(2);
    expect(text(inMargin[0])).toContain("Saved from your JSON or edits");
    expect(container.querySelector(".bj-doc-summary")).toBeNull();
  });

  it("from 960 to 1279px there is no margin: each trail sits under its block, and the margin's contents are one compact summary at the top", async () => {
    installViewport(1100);
    describeAccount();
    const { container } = page();
    await settle();
    const layout = query(container, ".bj-doc");
    expect(layout.classList.contains("bj-doc--inline")).toBe(true);
    expect(layout.querySelector(".bj-doc-margin")).toBeNull();
    const summary = query(layout, '[role="group"][aria-label="Current state"]');
    expect(text(summary)).toBe("Profile active since Oct 12 · 4 versions saved");
    expect(query(query(container, "#profile"), ".bj-trail")).toBeDefined();
  });

  it("opens a receipt inline there, as a disclosure under the line", async () => {
    installViewport(1100);
    describeAccount();
    const { container } = page();
    await settle();
    const opener = query<HTMLButtonElement>(query(container, "#profile"), "button.bj-trail-trigger");
    click(opener);
    expect(opener.getAttribute("aria-expanded")).toBe("true");
    expect(query(container, ".bj-trail-receipt .bj-receipt")).toBeDefined();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("under 960px each trail sits under its block and its receipt opens as a sheet that closes with Esc and gives focus back", async () => {
    installViewport(375);
    describeAccount();
    const { container } = page();
    await settle();
    expect(query(container, ".bj-doc").classList.contains("bj-doc--sheet")).toBe(true);
    const opener = query<HTMLButtonElement>(query(container, "#profile"), "button.bj-trail-trigger");
    opener.focus();
    click(opener);
    const sheet = query(document.body, '[role="dialog"]');
    expect(text(sheet)).toContain("3f2a9c1e");
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});

// ── failure and partial ────────────────────────────────────────────────────

describe("when the profile cannot be read", () => {
  it("says so in the band (G1), names the call and the time, and holds the page's one cyan button: Try again", async () => {
    describeAccount({ current: new FakeApiError("Internal error", "INTERNAL_ERROR", undefined, 500) });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Couldn't check your setup. You didn't do anything wrong, and nothing was lost.");
    expect(text(band(container))).toMatch(/why: GET \/profile\/current failed · \d\d:\d\d/);
    expect(cyan(container).map((button) => text(button))).toEqual(["Try again"]);
  });

  it("names what failed in the body, as an alert with no Retry of its own, and still offers the account card", async () => {
    describeAccount({ current: new FakeApiError("Internal error", "INTERNAL_ERROR", undefined, 500) });
    const { container } = page();
    await settle();
    const alert = query(container, ".bj-state--failure");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(text(alert)).toContain("Couldn't read your profile (Internal error). You didn't do anything wrong, and nothing was lost.");
    expect(alert.querySelector("button")).toBeNull();
    expect(container.querySelector(".bj-account-delete")).not.toBeNull();
  });

  it("reads again from Try again, and shows the profile when it answers", async () => {
    describeAccount({ current: new FakeApiError("Internal error", "INTERNAL_ERROR", undefined, 500) });
    const { container } = page();
    await settle();
    describeAccount();
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(text(container)).toContain("Asha Verma");
    expect(callsTo("GET /profile/current")).toHaveLength(2);
    // the keys were read fine the first time: only what failed is read again
    expect(callsTo("GET /credentials")).toHaveLength(1);
  });

  it("treats a failed credentials read as a setup check that failed too, while the profile still shows", async () => {
    describeAccount({ credentials: new FakeApiError("Down", "INTERNAL_ERROR", undefined, 500) });
    const { container } = page();
    await settle();
    expect(text(band(container))).toMatch(/why: GET \/credentials failed · \d\d:\d\d/);
    expect(text(container)).toContain("Asha Verma");
  });
});

describe("with no usable model key", () => {
  it("lights G3, which links to the model section of Settings", async () => {
    describeAccount({ credentials: [] });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Add a model key so Between Jobs can draft and score.");
    expect(text(band(container))).toContain("why: profile active · no model key saved");
    const link = named(band(container), "Add a model key") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/settings#ai-provider");
    expect(cyan(container)).toEqual([link]);
  });
});

describe("when one card cannot read its own part", () => {
  it("leaves the page and the band as they were, shows that card's alert with Retry and a red-cross trail, and keeps the others", async () => {
    describeAccount({ document: new FakeApiError("The resume service is not answering", "INTERNAL_ERROR", undefined, 500) });
    const { container } = page();
    await settle();
    expect(text(band(container))).toContain("Your profile is active.");
    expect(text(container)).toContain("Senior Data Engineer");
    const alerts = [...container.querySelectorAll(".bj-state--failure")];
    expect(alerts.length).toBe(3);
    expect(alerts.every((alert) => text(alert).includes("The resume service is not answering"))).toBe(true);
    expect(trails(container).filter((line) => line.startsWith("✕"))).toHaveLength(3);
    expect(cyan(container)).toHaveLength(0);
  });

  it("says a header preview built from an earlier profile version is one", async () => {
    describeAccount({ document: { ...DOCUMENT, profile_version_id: "00000000-old" } });
    const { container } = page();
    await settle();
    expect(text(query(container, "#resume-header"))).toContain("This preview uses an earlier version of your profile.");
  });

  it("does not render the Resume layout cards' setup notice as a failure when no profile is in use", async () => {
    describeAccount({ current: NOT_FOUND, document: new FakeApiError("Import a profile first.", "SETUP_REQUIRED", undefined, 409) });
    const { container } = page();
    await settle();
    expect(container.querySelector("#resume-header")).toBeNull();
  });
});

// ── the JSON import ────────────────────────────────────────────────────────

const PENDING = {
  id: "pending-1",
  canonical_json: { personal: { name: "Asha Verma", headline: "Data engineer" } },
  activated_at: null,
  created_at: "2026-10-19T11:55:00Z",
  source_kind: "web_json_paste",
  stats: { experience: 2, projects: 1, education: 1, skills: 8 },
  warnings: ["No certification entries."],
  version_count: 5,
};

const jsonImport = (container: ParentNode) => query(container, "#json-import");

async function importJson(container: HTMLElement, raw = '{"personal":{"name":"Asha Verma"}}') {
  typeInto(query<HTMLTextAreaElement>(jsonImport(container), "textarea"), raw);
  click(buttonNamed(jsonImport(container), "Import"));
  await settle();
}

describe("the JSON import for a person with no profile", () => {
  beforeEach(() => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
  });

  it("sends the pasted text, and replaces the body with the preview: counts, warnings, a trail and two buttons", async () => {
    const { container } = page();
    await settle();
    await importJson(container, "  raw text  ");
    expect(callsTo("POST /profile/versions")[0].body).toEqual({ raw_text: "  raw text  " });
    const review = query(container, "#import-review");
    expect(text(query(review, "h2"))).toBe("Review before activating");
    expect(text(review)).toContain("This draft is saved but not in use. It was read by code, with no model involved in the reading.");
    expect([...review.querySelectorAll(".bj-stat")].map((stat) => text(stat))).toEqual(["Experience2", "Projects1", "Education1", "Skills8"]);
    expect(text(review)).toContain("Note: No certification entries.");
    expect(trails(container)).toContain("○ Parsed by code · no model involved · Oct 19 · 1 warning");
    expect(container.querySelector("#set-up")).toBeNull();
    expect(container.querySelector(".bj-account-delete")).not.toBeNull();
  });

  it("puts the band on the review, with the review's own step and the page's only cyan button", async () => {
    const { container } = page();
    await settle();
    await importJson(container);
    expect(text(band(container))).toContain("Check what was read, then make this your profile.");
    expect(text(band(container))).toContain("why: import waiting for review · parsed by code, no model · draft inactive");
    const button = buttonNamed(band(container), "Confirm and make active");
    expect(cyan(container)).toEqual([button]);
    const own = buttonNamed(query(container, "#import-review"), "Looks good -- activate");
    expect(own.classList.contains("bj-primary")).toBe(false);
  });

  it("activates from the band's button: the same request as the review's own, then reads the profile again and shows it", async () => {
    on("POST /profile/versions/pending-1/activate", {});
    const { container } = page();
    await settle();
    await importJson(container);
    on("GET /profile/current", profile({ id: "pending-1", activated_at: "2026-10-19T12:00:00Z", created_at: "2026-10-19T11:55:00Z", version_count: 5 }));
    click(buttonNamed(band(container), "Confirm and make active"));
    await settle();
    expect(callsTo("POST /profile/versions/pending-1/activate")).toHaveLength(1);
    expect(text(container)).toContain("Asha Verma");
    expect(container.querySelector("#import-review")).toBeNull();
    expect(text(band(container))).toContain("Your profile is active.");
    // the button that was pressed is gone: focus goes to the profile that now shows, not to the top of the page
    expect(document.activeElement).toBe(query(container, "#bj-profile-name"));
  });

  it("activates from the review's own button too", async () => {
    on("POST /profile/versions/pending-1/activate", {});
    const { container } = page();
    await settle();
    await importJson(container);
    on("GET /profile/current", profile({ id: "pending-1" }));
    click(buttonNamed(container, "Looks good -- activate"));
    await settle();
    expect(callsTo("POST /profile/versions/pending-1/activate")).toHaveLength(1);
    expect(text(container)).toContain("Your profile is active.");
  });

  it("says Activating... on the band's button while the request is out, and ignores a second press", async () => {
    let finish: (value: unknown) => void = () => undefined;
    on("POST /profile/versions/pending-1/activate", () => new Promise((resolve) => (finish = resolve)));
    const { container } = page();
    await settle();
    await importJson(container);
    click(buttonNamed(band(container), "Confirm and make active"));
    await settle(1);
    const pressed = buttonNamed(band(container), "Activating...");
    expect(pressed.getAttribute("aria-disabled")).toBe("true");
    click(pressed);
    await settle(1);
    expect(callsTo("POST /profile/versions/pending-1/activate")).toHaveLength(1);
    on("GET /profile/current", profile({ id: "pending-1" }));
    finish({});
    await settle();
  });

  it("shows a failed activation under the review, keeps the review, and lets the person try again", async () => {
    on("POST /profile/versions/pending-1/activate", new FakeApiError("Could not activate", "INTERNAL_ERROR", undefined, 500));
    const { container } = page();
    await settle();
    await importJson(container);
    click(buttonNamed(container, "Looks good -- activate"));
    await settle();
    expect(text(query(container, "#import-review [role='alert']"))).toBe("Could not activate");
    expect(container.querySelector("#import-review")).not.toBeNull();
  });

  it("shows an import the importer refused on the import section, and never the preview", async () => {
    on("POST /profile/versions", new FakeApiError("personal.name: field required", "INVALID_INPUT", undefined, 422));
    const { container } = page();
    await settle();
    await importJson(container);
    expect(text(query(container, "#set-up [role='alert']"))).toBe("personal.name: field required");
    expect(container.querySelector("#import-review")).toBeNull();
  });

  it("cancels the draft and reads again: with no profile the page is the import section again", async () => {
    on("DELETE /profile/versions/pending-1", undefined);
    const { container } = page();
    await settle();
    await importJson(container);
    click(buttonNamed(container, "Cancel"));
    await settle();
    expect(callsTo("DELETE /profile/versions/pending-1")).toHaveLength(1);
    expect(container.querySelector("#set-up")).not.toBeNull();
    expect(container.querySelector("#import-review")).toBeNull();
    expect(document.activeElement).toBe(query(query(container, "#set-up"), "h2"));
  });

  it("keeps the review, with the failure in words, when the cancel fails on the network or a 5xx, and pressing Cancel again is the retry", async () => {
    on("DELETE /profile/versions/pending-1", new FakeApiError("The server did not answer", "INTERNAL_ERROR", undefined, 500));
    const { container } = page();
    await settle();
    await importJson(container);
    click(buttonNamed(container, "Cancel"));
    await settle();
    expect(text(query(container, "#import-review [role='alert']"))).toBe("The server did not answer");
    expect(callsTo("GET /profile/current")).toHaveLength(1);
    on("DELETE /profile/versions/pending-1", undefined);
    click(buttonNamed(container, "Cancel"));
    await settle();
    expect(container.querySelector("#set-up")).not.toBeNull();
  });

  it.each(["NOT_FOUND", "CONFLICT"])("reloads the truth, without a word, when the cancel says the draft is already gone or in use (%s)", async (code) => {
    on("DELETE /profile/versions/pending-1", new FakeApiError("gone", code, undefined, code === "CONFLICT" ? 409 : 404));
    const { container } = page();
    await settle();
    await importJson(container);
    on("GET /profile/current", profile({ id: "pending-1" }));
    click(buttonNamed(container, "Cancel"));
    await settle();
    expect(text(container)).toContain("Your profile is active.");
    expect(container.querySelector("#import-review")).toBeNull();
  });
});

describe("the JSON import says only what is true about the draft", () => {
  it("calls the band's button Cancelling... while a draft is being thrown away, never Activating..., and ignores a press on it", async () => {
    let finish: (value: unknown) => void = () => undefined;
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    on("DELETE /profile/versions/pending-1", () => new Promise((resolve) => (finish = resolve)));
    on("POST /profile/versions/pending-1/activate", {});
    const { container } = page();
    await settle();
    await importJson(container);
    click(buttonNamed(container, "Cancel"));
    await settle(1);
    expect(text(band(container))).not.toContain("Activating...");
    const pressed = buttonNamed(band(container), "Cancelling...");
    expect(pressed.getAttribute("aria-disabled")).toBe("true");
    click(pressed);
    await settle(1);
    expect(callsTo("POST /profile/versions/pending-1/activate")).toHaveLength(0);
    finish(undefined);
    await settle();
    expect(container.querySelector("#set-up")).not.toBeNull();
  });

  it("moves keyboard focus to the review's heading when it opens, since the Import button that was pressed is gone", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    await importJson(container);
    expect(document.activeElement).toBe(query(query(container, "#import-review"), "h2"));
  });

  it("does not draw a pending draft for JSON that is the profile in use: it says so on the import section and the band stays as it was", async () => {
    describeAccount();
    on("POST /profile/versions", { ...profile(), stats: { experience: 1 }, warnings: [] });
    const { container } = page();
    await settle();
    await importJson(container);
    expect(container.querySelector("#import-review")).toBeNull();
    expect(container.querySelector(".bj-badge-gold")).toBeNull();
    expect(text(jsonImport(container))).toContain("This matches your current profile: nothing to change.");
    expect(text(query(jsonImport(container), "[role='status']"))).toBe("This matches your current profile: nothing to change.");
    expect(text(band(container))).toContain("Your profile is active.");
    expect(text(band(container))).not.toContain("draft inactive");
    expect(cyan(container)).toHaveLength(0);
    expect(container.querySelector(".bj-profile-identity")).not.toBeNull();
    // a later import is a new question: the note goes
    on("POST /profile/versions", PENDING);
    click(buttonNamed(jsonImport(container), "Import"));
    await settle();
    expect(container.querySelector("#import-review")).not.toBeNull();
  });

  it("still draws a draft for JSON that matches an older, replaced version: that one can be made active again", async () => {
    describeAccount();
    on("POST /profile/versions", profile({ id: "older-version", stats: {}, warnings: [] }));
    const { container } = page();
    await settle();
    await importJson(container);
    expect(container.querySelector("#import-review")).not.toBeNull();
    expect(text(band(container))).toContain("draft inactive");
  });

  it("does not leave a failed activation's message anywhere once the next try works", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    let tries = 0;
    on("POST /profile/versions/pending-1/activate", () => {
      tries += 1;
      if (tries === 1) throw new FakeApiError("Could not activate", "INTERNAL_ERROR", undefined, 500);
      return {};
    });
    const { container } = page();
    await settle();
    await importJson(container);
    click(buttonNamed(container, "Looks good -- activate"));
    await settle();
    expect(text(query(container, "#import-review [role='alert']"))).toBe("Could not activate");
    on("GET /profile/current", profile({ id: "pending-1" }));
    click(buttonNamed(container, "Looks good -- activate"));
    await settle();
    expect(container.querySelector("#import-review")).toBeNull();
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(text(container)).not.toContain("Could not activate");
  });

  it("keeps a failed activation off the import section, and a refused import off the review", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", new FakeApiError("personal.name: field required", "INVALID_INPUT", undefined, 422));
    const { container } = page();
    await settle();
    await importJson(container);
    expect(container.querySelectorAll("[role='alert']")).toHaveLength(1);
    on("POST /profile/versions", PENDING);
    on("POST /profile/versions/pending-1/activate", new FakeApiError("Could not activate", "INTERNAL_ERROR", undefined, 500));
    await importJson(container);
    // the refusal of the first import is gone with the second, and the review shows nothing of it
    expect(text(query(container, "#import-review"))).not.toContain("personal.name");
    click(buttonNamed(container, "Looks good -- activate"));
    await settle();
    expect(container.querySelectorAll("[role='alert']")).toHaveLength(1);
    expect(query(container, "#import-review [role='alert']")).not.toBeNull();
  });

  it("clears a refused JSON import's message when a resume file's draft arrives", async () => {
    describeAccount({ current: NOT_FOUND });
    upload.fetchBytes.mockResolvedValue(DRAFT);
    on("POST /profile/versions", new FakeApiError("personal.name: field required", "INVALID_INPUT", undefined, 422));
    const { container } = page();
    await settle();
    await importJson(container);
    expect(text(query(container, "#set-up [role='alert']"))).toBe("personal.name: field required");
    await readFile(container);
    expect(container.querySelector("#import-review")).not.toBeNull();
    expect(text(container)).not.toContain("personal.name: field required");
  });

  it("tells the band's one-step button apart by saying it replaces the current profile, when one is in use", async () => {
    describeAccount();
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    await importJson(container);
    expect(text(band(container))).toContain("Check what was read, then make this your profile. It replaces your current one.");
  });

  it("says nothing of replacing when there is no profile to replace", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    await importJson(container);
    expect(text(band(container))).not.toContain("replaces");
  });

  it("describes the saved draft in the import section's own words and the review's, without saying nothing is saved", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    expect(text(jsonImport(container))).toContain("saved as an inactive draft that you review before it becomes your profile");
    expect(text(jsonImport(container))).not.toContain("before anything is saved");
    await importJson(container);
    expect(text(query(container, "#import-review"))).not.toContain("before saving");
  });
});

describe("a failed re-read after the profile changed replaces the page and says so", () => {
  const FAILED_READ = new FakeApiError("Internal error", "INTERNAL_ERROR", undefined, 500);

  function expectFailedPage(container: HTMLElement) {
    expect(text(band(container))).toContain("Couldn't check your setup.");
    expect(text(band(container))).toMatch(/why: GET \/profile\/current failed/);
    expect(text(query(container, ".bj-state--failure"))).toContain("Couldn't read your profile (Internal error).");
    expect(cyan(container).map((button) => text(button))).toEqual(["Try again"]);
    expect(container.querySelector(".bj-profile-identity")).toBeNull();
  }

  it("after an edit is saved: the dialog is closed", async () => {
    describeAccount();
    on("POST /profile/versions", { id: "v5" });
    on("POST /profile/versions/v5/activate", {});
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Edit Senior Data Engineer at Acme Analytics"));
    const dialog = query(document.body, '[role="dialog"]');
    typeInto(query(dialog, 'input[type="text"]'), "Staff Data Engineer");
    on("GET /profile/current", FAILED_READ);
    click(buttonNamed(dialog, "Save"));
    await settle();
    expectFailedPage(container);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("after the JSON preview is activated", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    on("POST /profile/versions/pending-1/activate", {});
    const { container } = page();
    await settle();
    await importJson(container);
    on("GET /profile/current", FAILED_READ);
    click(buttonNamed(container, "Looks good -- activate"));
    await settle();
    expectFailedPage(container);
    expect(container.querySelector("#import-review")).toBeNull();
  });

  it("after the JSON preview is cancelled", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    on("DELETE /profile/versions/pending-1", undefined);
    const { container } = page();
    await settle();
    await importJson(container);
    on("GET /profile/current", FAILED_READ);
    click(buttonNamed(container, "Cancel"));
    await settle();
    expectFailedPage(container);
    expect(container.querySelector("#import-review")).toBeNull();
  });
});

describe("the band's Try again reads again only what failed", () => {
  it("keeps a waiting JSON preview when only the keys failed to read", async () => {
    describeAccount({ current: NOT_FOUND, credentials: new FakeApiError("Down", "INTERNAL_ERROR", undefined, 500) });
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    await importJson(container);
    expect(text(band(container))).toMatch(/why: GET \/credentials failed/);
    expect(container.querySelector("#import-review")).not.toBeNull();
    on("GET /credentials", READY_KEY);
    click(buttonNamed(band(container), "Try again"));
    await settle();
    expect(container.querySelector("#import-review")).not.toBeNull();
    expect(callsTo("GET /profile/current")).toHaveLength(1);
    expect(callsTo("GET /credentials")).toHaveLength(2);
    expect(text(band(container))).toContain("Check what was read, then make this your profile.");
  });
});

describe("the band's link to a hash already in the address", () => {
  it("scrolls again when it is followed a second time", async () => {
    describeAccount({ current: NOT_FOUND });
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    const { container } = page();
    await settle();
    expect(scrolled).not.toHaveBeenCalled();
    click(named(band(container), "Set up your profile"));
    await settle();
    expect(scrolled).toHaveBeenCalledTimes(1);
    click(named(band(container), "Set up your profile"));
    await settle();
    expect(scrolled).toHaveBeenCalledTimes(2);
  });
});

describe("the JSON import for a person who has a profile", () => {
  it("replaces the page's body with the preview (so nothing can be edited against a profile about to be replaced), under the band's IR1", async () => {
    describeAccount();
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    await importJson(container);
    expect(container.querySelector("#import-review")).not.toBeNull();
    expect(container.querySelector(".bj-profile-identity")).toBeNull();
    expect(text(band(container))).toContain("Check what was read, then make this your profile.");
    expect(cyan(container).map((button) => text(button))).toEqual(["Confirm and make active"]);
  });

  it("puts G3 first when there is no model key, since a JSON import needs none and the band follows the global rows", async () => {
    describeAccount({ credentials: [] });
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    await importJson(container);
    expect(text(band(container))).toContain("Add a model key so Between Jobs can draft and score.");
    expect(cyan(container)).toHaveLength(1);
    expect(container.querySelector("#import-review")).not.toBeNull();
  });
});

// ── a resume file's draft ──────────────────────────────────────────────────

const TEXT = "Pat Example\nData Engineer\nAcme Corp 2022 - present\n";
const span = (value: string) => ({ start: TEXT.indexOf(value), end: TEXT.indexOf(value) + value.length });
const DRAFT = {
  version_id: "11112222-3333-4444-5555-666677778888",
  already_active: false,
  profile: { personal: { name: "Pat Example", headline: "Data Engineer" }, experience: [{ title: "Data Engineer", company: "Acme Corp", start_date: "2022-01", end_date: "present", is_current: true }] },
  span_unit: "utf16",
  source_spans: { "/personal/name": span("Pat Example"), "/personal/headline": span("Data Engineer"), "/experience/0/company": span("Acme Corp") },
  dropped: [{ path: "/skills/tools/0", reason: "not_in_document", detail: "this text is not in the document", value: "HyperWidget" }],
  assumptions: [],
  extracted_text: TEXT,
  stats: { kept_fields: 9, dropped_fields: 1 },
  warnings: [],
  document: { kind: "pdf", filename: "cv.pdf", pages_total: 1, pages_read: 1, truncated: false },
};

// How many values of the fixture the person has to check one by one (the ones read from the file).
const PARSED = parseImportResponse(DRAFT);
const FIELDS = fieldsToCheck(buildReview(PARSED?.profile ?? { personal: { name: "" } }, PARSED?.spans ?? {})).length;

function chooseFile(container: HTMLElement) {
  const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37]);
  const file = { name: "cv.pdf", size: bytes.length, slice: () => ({ arrayBuffer: async () => bytes.buffer as ArrayBuffer }) };
  const input = query<HTMLInputElement>(container, 'input[type="file"][accept*=".pdf"]');
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  act(() => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function readFile(container: HTMLElement) {
  chooseFile(container);
  await settle(5);
}

const checkboxes = (container: ParentNode) => [...container.querySelectorAll<HTMLInputElement>("#import-review .bj-import-check input")];

describe("a resume file's draft for a person with a profile", () => {
  beforeEach(() => {
    describeAccount();
    upload.fetchBytes.mockResolvedValue(DRAFT);
  });

  it("sends the file as bytes, and shows the draft in its card with its trail, leaving the profile above it", async () => {
    const { container } = page();
    await settle();
    await readFile(container);
    expect(upload.fetchBytes).toHaveBeenCalledTimes(1);
    expect(upload.fetchBytes.mock.calls[0][0]).toBe("/profile/import-document?filename=resume.pdf");
    expect(upload.fetchBytes.mock.calls[0][2]).toBe("application/pdf");
    const review = query(container, "#import-review");
    expect(text(query(review, "h3"))).toBe("Check what was read from your file");
    expect(trails(container)).toContain(`✦ Read by your model · from your PDF file, probabilistic · ${FIELDS} fields to check`);
    expect(container.querySelector(".bj-profile-identity")).not.toBeNull();
    expect(container.querySelector(".bj-account-delete")).not.toBeNull();
  });

  it("tells the band an import is waiting: PR1, whose link to the review is the page's only cyan", async () => {
    const { container } = page();
    await settle();
    await readFile(container);
    expect(text(band(container))).toContain("Your import is waiting. Check every field, then activate.");
    expect(text(band(container))).toContain("why: import waiting for review · converted by your model, probabilistic · not active until you confirm");
    const link = named(band(container), "Review the import") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/profile#import-review");
    expect(cyan(container)).toEqual([link]);
    expect(buttonNamed(container, "Use this profile").classList.contains("bj-primary")).toBe(false);
  });

  it("holds the JSON import back while the draft waits, and says why", async () => {
    const { container } = page();
    await settle();
    await readFile(container);
    expect(query<HTMLTextAreaElement>(jsonImport(container), "textarea").disabled).toBe(true);
    expect(text(jsonImport(container))).toContain("A file import above is in progress.");
    expect(buttonNamed(jsonImport(container), "Import").disabled).toBe(true);
  });

  it("goes to the review when the link is followed: the band becomes IR1, locked, and focus lands on the review's heading", async () => {
    const { container } = page();
    await settle();
    await readFile(container);
    click(named(band(container), "Review the import"));
    await settle();
    expect(document.activeElement).toBe(query(query(container, "#import-review"), "h3"));
    expect(text(band(container))).toContain("Check every field, then make this your profile.");
    expect(text(band(container))).toContain(`why: ${FIELDS} fields found · 1 left out · ${FIELDS} still to check · draft inactive`);
    const locked = band(container).querySelector<HTMLButtonElement>("button.bj-band-locked");
    expect(locked?.getAttribute("aria-disabled")).toBe("true");
    expect(text(locked as HTMLElement)).toBe("Confirm and make active");
    expect(text(band(container))).toContain(`Check the ${FIELDS} remaining fields to unlock this.`);
    expect(cyan(container)).toHaveLength(0);
  });

  it("does nothing when the locked band button is pressed", async () => {
    const { container } = page("/profile#import-review");
    await settle();
    await readFile(container);
    click(band(container).querySelector("button.bj-band-locked") as HTMLElement);
    await settle();
    expect(callsTo("POST /profile/versions/11112222-3333-4444-5555-666677778888/activate")).toHaveLength(0);
  });

  it("counts down as each field is checked, in the band and in the card, and unlocks the band's button only at the last", async () => {
    const { container } = page("/profile#import-review");
    await settle();
    await readFile(container);
    expect(checkboxes(container)).toHaveLength(FIELDS);
    for (const box of checkboxes(container).slice(0, FIELDS - 1)) click(box);
    expect(text(band(container))).toContain("1 still to check");
    expect(text(band(container))).toContain("Check the 1 remaining field to unlock this.");
    expect(text(query(container, "#bj-import-progress"))).toContain(`1 of ${FIELDS} fields still to check`);
    click(checkboxes(container)[FIELDS - 1]);
    expect(text(band(container))).toContain("0 still to check");
    expect(band(container).querySelector("button.bj-band-locked")).toBeNull();
    expect(cyan(container).map((button) => text(button))).toEqual(["Confirm and make active"]);
    expect(buttonNamed(container, "Use this profile").hasAttribute("aria-disabled")).toBe(false);
  });

  it("uses the draft from the band's button once every field is checked: one activation request, then the page reads the profile again", async () => {
    on("POST /profile/versions/11112222-3333-4444-5555-666677778888/activate", {});
    const { container } = page("/profile#import-review");
    await settle();
    await readFile(container);
    for (const box of checkboxes(container)) click(box);
    on("GET /profile/current", profile({ id: DRAFT.version_id, version_count: 5 }));
    click(buttonNamed(band(container), "Confirm and make active"));
    await settle(5);
    expect(callsTo("POST /profile/versions/11112222-3333-4444-5555-666677778888/activate")).toHaveLength(1);
    expect(callsTo("GET /profile/current")).toHaveLength(2);
    expect(text(container)).toContain("Your profile now comes from your file.");
    expect(container.querySelector("#import-review")).toBeNull();
    expect(text(band(container))).toContain("Your profile is active.");
  });

  it("says Activating... on the band's button while the request is out", async () => {
    let finish: (value: unknown) => void = () => undefined;
    on("POST /profile/versions/11112222-3333-4444-5555-666677778888/activate", () => new Promise((resolve) => (finish = resolve)));
    const { container } = page("/profile#import-review");
    await settle();
    await readFile(container);
    for (const box of checkboxes(container)) click(box);
    click(buttonNamed(band(container), "Confirm and make active"));
    await settle(2);
    expect(buttonNamed(band(container), "Activating...").getAttribute("aria-disabled")).toBe("true");
    on("GET /profile/current", profile());
    finish({});
    await settle(5);
  });

  it("discards the draft from its own button, and the band goes back to PR0", async () => {
    on("DELETE /profile/versions/11112222-3333-4444-5555-666677778888", undefined);
    const { container } = page();
    await settle();
    await readFile(container);
    click(buttonNamed(container, "Discard this draft"));
    await settle();
    expect(callsTo("DELETE /profile/versions/11112222-3333-4444-5555-666677778888")).toHaveLength(1);
    expect(text(band(container))).toContain("Your profile is active.");
    expect(container.querySelector("#import-review")).toBeNull();
  });

  it("shows an unreadable file's failure in the card, with the card's own words and its own Try again, and lights nothing in the band", async () => {
    upload.fetchBytes.mockRejectedValue(new FakeApiError("That PDF is password-protected.", "INVALID_INPUT", undefined, 422, false));
    const { container } = page();
    await settle();
    await readFile(container);
    expect(text(query(container, ".bj-import-failure"))).toContain("That PDF is password-protected.");
    expect(text(band(container))).toContain("Your profile is active.");
    expect(cyan(container)).toHaveLength(0);
  });

  it("shows the model-key setup notice with its link when the upload needs a key", async () => {
    upload.fetchBytes.mockRejectedValue(
      Object.assign(new FakeApiError("'profile_import' has no model.", "SETUP_REQUIRED", undefined, 409), { capability: "profile_import", settingsPath: "/settings" }),
    );
    const { container } = page();
    await settle();
    await readFile(container);
    const failure = query(container, ".bj-import-failure");
    expect(text(failure)).toContain("Reading a resume file needs your own AI model.");
    expect(failure.querySelector("a")?.getAttribute("href")).toBe("/settings");
  });
});

describe("the mark that a model read something", () => {
  it("is not drawn on the idle file import card, where no model has acted, and is once a draft exists", async () => {
    describeAccount({ current: NOT_FOUND });
    upload.fetchBytes.mockResolvedValue(DRAFT);
    const { container } = page();
    await settle();
    expect(container.querySelector(".bj-badge-violet")).toBeNull();
    await readFile(container);
    expect(text(query(container, ".bj-badge-violet"))).toBe("Read by an AI model");
  });

  it("names both buttons that use the draft, since the band's one-step button also does", async () => {
    describeAccount({ current: NOT_FOUND });
    upload.fetchBytes.mockResolvedValue(DRAFT);
    const { container } = page();
    await settle();
    await readFile(container);
    expect(text(query(container, "#import-review .bj-import-warning"))).toContain('"Use this profile" below or "Confirm and make active" in the next-step band');
  });

  it("tells the band's one-step button apart by saying a file draft replaces the profile in use", async () => {
    describeAccount();
    upload.fetchBytes.mockResolvedValue(DRAFT);
    const { container } = page("/profile#import-review");
    await settle();
    await readFile(container);
    expect(text(band(container))).toContain("Check every field, then make this your profile. It replaces your current one.");
  });
});

describe("the file import card with no usable model key", () => {
  it("says what is missing and links to where it is fixed, beside the band's own G3, and offers no way to send a file", async () => {
    describeAccount({ credentials: [] });
    const { container } = page();
    await settle();
    const card = query(container, "#resume-import");
    expect(text(query(card, ".bj-state--setup"))).toContain("Importing from a resume file uses your own AI model");
    expect(query<HTMLAnchorElement>(card, ".bj-state--setup a").getAttribute("href")).toBe("/settings#ai-provider");
    expect(buttonNamed(card, "Choose a file").getAttribute("aria-disabled")).toBe("true");
    expect(cyan(container)).toHaveLength(1);
  });

  it("says nothing while the keys are still being read, or when one is usable", async () => {
    on("GET /profile/current", profile());
    on("GET /credentials", () => new Promise(() => undefined));
    on("GET /resume-documents/mine", DOCUMENT);
    on("POST /resume-documents/doc-1/header/preview", { chips: [] });
    const loading = page();
    await settle();
    expect(loading.container.querySelector("#resume-import .bj-state--setup")).toBeNull();
    loading.unmount();
    describeAccount();
    const ready = page();
    await settle();
    expect(ready.container.querySelector("#resume-import .bj-state--setup")).toBeNull();
  });
});

describe("a resume file's draft for a person with no profile", () => {
  it("puts the band on the review at once (nothing else is on the page), locked until every field is checked", async () => {
    describeAccount({ current: NOT_FOUND });
    upload.fetchBytes.mockResolvedValue(DRAFT);
    const { container } = page();
    await settle();
    await readFile(container);
    expect(text(band(container))).toContain("Check every field, then make this your profile.");
    expect(band(container).querySelector("button.bj-band-locked")).not.toBeNull();
    expect(cyan(container)).toHaveLength(0);
    expect(text(band(container))).not.toContain("Your import is waiting");
  });

  it("makes the draft the profile once every field is checked, from the band, with the first activation of the account", async () => {
    describeAccount({ current: NOT_FOUND });
    upload.fetchBytes.mockResolvedValue(DRAFT);
    on("POST /profile/versions/11112222-3333-4444-5555-666677778888/activate", {});
    const { container } = page();
    await settle();
    await readFile(container);
    for (const box of checkboxes(container)) click(box);
    on("GET /profile/current", profile({ id: DRAFT.version_id }));
    click(buttonNamed(band(container), "Confirm and make active"));
    await settle(5);
    expect(text(container)).toContain("Asha Verma");
    expect(text(container)).toContain("It is your active profile.");
  });
});

// ── editing ────────────────────────────────────────────────────────────────

describe("editing: every save is a new version through the importer", () => {
  beforeEach(() => describeAccount());

  it("saves an entry as the next version: imports the edited profile, activates it, reads again and closes the dialog", async () => {
    on("POST /profile/versions", { id: "v5" });
    on("POST /profile/versions/v5/activate", {});
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Edit Senior Data Engineer at Acme Analytics"));
    const dialog = query(document.body, '[role="dialog"]');
    expect(text(dialog)).toContain("Saving creates version 5");
    typeInto(query(dialog, 'input[type="text"]'), "Staff Data Engineer");
    on("GET /profile/current", profile({ id: "v5", version_count: 5 }));
    click(buttonNamed(dialog, "Save"));
    await settle();
    const sent = JSON.parse((callsTo("POST /profile/versions")[0].body as { raw_text: string }).raw_text) as { experience: { title: string }[] };
    expect(sent.experience[0].title).toBe("Staff Data Engineer");
    expect(callsTo("POST /profile/versions/v5/activate")).toHaveLength(1);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("keeps the dialog open with the importer's own refusal when a save is refused", async () => {
    on("POST /profile/versions", new FakeApiError("experience: at least one piece of evidence is required", "INVALID_INPUT", undefined, 422));
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Edit Senior Data Engineer at Acme Analytics"));
    click(buttonNamed(query(document.body, '[role="dialog"]'), "Save"));
    await settle();
    const dialog = query(document.body, '[role="dialog"]');
    expect(text(query(dialog, '[role="alert"]'))).toBe("experience: at least one piece of evidence is required");
  });

  it("shows a refused removal above the sections, and a refused Personal details save under that card only", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    on("POST /profile/versions", new FakeApiError("nope", "INVALID_INPUT", undefined, 422));
    const { container } = page();
    await settle();
    click(buttonNamed(container, "Delete Senior Data Engineer at Acme Analytics"));
    await settle();
    expect(text(query(container, "#profile-content [role='alert']"))).toContain("That change was not saved: nope");
    expect(query(container, "#personal-details").querySelector('[role="alert"]')).toBeNull();

    click(query(container, "#profile-content [role='alert'] button"));
    typeInto(query(container, "#personal-details textarea"), "Citizen, no sponsorship");
    click(buttonNamed(query(container, "#personal-details"), "Save"));
    await settle();
    expect(text(query(container, "#personal-details [role='alert']"))).toBe("nope");
    expect(query(container, "#profile-content").querySelector('[role="alert"]')).toBeNull();
  });

  it("saves Personal details through the same pipeline, and its Save is outlined", async () => {
    on("POST /profile/versions", { id: "v5" });
    on("POST /profile/versions/v5/activate", {});
    const { container } = page();
    await settle();
    const save = buttonNamed(query(container, "#personal-details"), "Save");
    expect(save.classList.contains("bj-primary")).toBe(false);
    typeInto(query(container, "#personal-details textarea"), "Citizen, no sponsorship");
    click(save);
    await settle();
    const sent = JSON.parse((callsTo("POST /profile/versions")[0].body as { raw_text: string }).raw_text) as { personal: { work_authorization: string } };
    expect(sent.personal.work_authorization).toBe("Citizen, no sponsorship");
  });
});

// ── the one cyan fill, the account and the accessibility of every state ────

describe("one cyan fill per page", () => {
  const states: [string, () => Promise<HTMLElement>, number][] = [
    ["active, nothing waiting", async () => {
      describeAccount();
      const { container } = page();
      await settle();
      return container;
    }, 0],
    ["no profile", async () => {
      describeAccount({ current: NOT_FOUND });
      const { container } = page();
      await settle();
      return container;
    }, 1],
    ["no model key", async () => {
      describeAccount({ credentials: [] });
      const { container } = page();
      await settle();
      return container;
    }, 1],
    ["the profile failed to load", async () => {
      describeAccount({ current: new FakeApiError("x", "INTERNAL_ERROR", undefined, 500) });
      const { container } = page();
      await settle();
      return container;
    }, 1],
  ];

  it.each(states)("%s: at most one, and it is the band's", async (_name, open, expected) => {
    const container = await open();
    const fills = cyan(container);
    expect(fills).toHaveLength(expected);
    for (const fill of fills) expect(band(container).contains(fill)).toBe(true);
    expect(container.querySelector("[style]")).toBeNull();
  });
});

describe("accessibility", () => {
  it.each([
    ["an active profile", () => describeAccount()],
    ["no profile", () => describeAccount({ current: NOT_FOUND })],
    ["a profile that failed to load", () => describeAccount({ current: new FakeApiError("x", "INTERNAL_ERROR", undefined, 500) })],
  ])("%s has no serious violation", async (_name, setup) => {
    setup();
    const { container } = page();
    await settle();
    await expectNoSeriousA11yViolations(container);
  });

  it("the JSON preview has none", async () => {
    describeAccount({ current: NOT_FOUND });
    on("POST /profile/versions", PENDING);
    const { container } = page();
    await settle();
    await importJson(container);
    await expectNoSeriousA11yViolations(container);
  });

  it("a resume file's review, with its checks and the locked button, has none", async () => {
    describeAccount();
    upload.fetchBytes.mockResolvedValue(DRAFT);
    const { container } = page("/profile#import-review");
    await settle();
    await readFile(container);
    await expectNoSeriousA11yViolations(container);
  });

  it("draws the controls in the order a keyboard meets them: Settings, the band's button, then the import controls, then the account", async () => {
    describeAccount({ current: NOT_FOUND });
    const { container } = page();
    await settle();
    const before = (first: Element, second: Element) => (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    const settings = named(container, "Settings");
    const bandButton = named(band(container), "Set up your profile");
    const chooseFile = named(container, "Choose a file");
    const importJsonButton = buttonNamed(jsonImport(container), "Import");
    const deleteAccount = query(container, ".bj-account-delete button");
    expect(before(settings, bandButton)).toBe(true);
    expect(before(bandButton, chooseFile)).toBe(true);
    expect(before(chooseFile, importJsonButton)).toBe(true);
    expect(before(importJsonButton, deleteAccount)).toBe(true);
    // nothing takes a tab stop out of the natural order
    expect([...container.querySelectorAll("[tabindex]")].filter((element) => Number(element.getAttribute("tabindex")) > 0)).toEqual([]);
  });

  it("closes a receipt in the margin with Esc and puts focus back on its line", async () => {
    installViewport(1400);
    describeAccount();
    const { container } = page();
    await settle();
    const line = query<HTMLButtonElement>(query(container, ".bj-doc-margin"), "button.bj-trail-trigger");
    line.focus();
    click(line);
    expect(line.getAttribute("aria-expanded")).toBe("true");
    keydown("Escape");
    expect(line.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(line);
  });
});
