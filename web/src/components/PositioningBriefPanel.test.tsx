// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FakeApiError, callsTo, on, reset } from "../testing/fakeApi";
import { settle, text } from "../testing/forms";
import { BRIEF, NOW, FakeSetupError } from "../testing/researchFixtures";
import { buttonNamed, click, query, render } from "../testing/render";
import { PositioningBriefPanel } from "./PositioningBriefPanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const PATH = "/applications/a1/positioning-brief";

function panel() {
  return render(
    <MemoryRouter>
      <PositioningBriefPanel applicationId="a1" now={NOW} />
    </MemoryRouter>,
  );
}

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("Your play", () => {
  it("shows a placeholder while the stored brief is read", () => {
    on(`GET ${PATH}`, () => new Promise(() => undefined));
    const { container } = panel();
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Loading your brief...");
  });

  it("teaches what a brief is and what it needs before the first build, and draws no sample brief and no trail", async () => {
    on(`GET ${PATH}`, { brief: null });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-empty h3"))).toBe("No brief yet");
    expect(text(query(container, ".bj-rs-empty"))).toContain("what to lead with");
    expect(text(query(container, ".bj-rs-empty"))).toContain("It needs a model key and an active profile.");
    expect(container.querySelector(".bj-rs-brief, .bj-trail")).toBeNull();
    expect(buttonNamed(container, "Build my positioning brief")).toBeTruthy();
  });

  it("draws the three parts, each of the first two with its citation as evidence -- gold, the gap's too, never red or green", async () => {
    on(`GET ${PATH}`, { brief: BRIEF });
    const { container } = panel();
    await settle();
    const parts = [...container.querySelectorAll(".bj-rs-part")];
    expect(parts.map((part) => text(part.querySelector("h3")!))).toEqual(["Lead with", "The gap that matters", "One project to close it"]);
    expect(parts.map((part) => text(part.querySelector(".bj-rs-text")!))).toEqual([BRIEF.lead_with, BRIEF.gap_that_matters, BRIEF.recommended_project]);
    expect(parts.map((part) => part.querySelector(".bj-rs-ev") === null ? null : text(part.querySelector(".bj-rs-ev")!))).toEqual(["◆ Evidence Data pipelines", "◆ Evidence Kafka", null]);
    expect(container.querySelector(".bj-badge-emerald, .bj-badge-danger")).toBeNull();
    expect([...container.querySelectorAll(".bj-rs-ev .bj-trail-glyph")].every((glyph) => glyph.classList.contains("bj-trail-glyph--gold"))).toBe(true);
  });

  it("says when it was built from the brief's own date, with a trail from the returned fields", async () => {
    on(`GET ${PATH}`, { brief: BRIEF });
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-rs-meta"))).toBe("Built Sep 20");
    expect(text(query(container, ".bj-trail"))).toBe("✦ Written by your model · on your model key · Sep 20");
    click(query(container, "button.bj-trail-trigger"));
    expect(text(query(container, ".bj-receipt"))).toContain("each citation is one of the strengths or gaps found for this role");
  });

  it("leaves the date out when it cannot be read, rather than guessing", async () => {
    on(`GET ${PATH}`, { brief: { ...BRIEF, created_at: "yesterday" } });
    const { container } = panel();
    await settle();
    expect(container.querySelector(".bj-rs-meta")).toBeNull();
    expect(text(query(container, ".bj-trail"))).toBe("✦ Written by your model · on your model key");
  });

  it("shows each flag the code check raised as a neutral callout above the brief and counts it on the trail", async () => {
    on(`GET ${PATH}`, { brief: { ...BRIEF, rubric_warnings: ["brief contains an outcome-promise phrase"] } });
    const { container } = panel();
    await settle();
    const flagged = query(container, ".bj-state--partial");
    expect(text(flagged)).toContain("A code check flagged this brief:");
    expect(text(flagged)).toContain("brief contains an outcome-promise phrase");
    expect(flagged.compareDocumentPosition(query(container, ".bj-rs-brief")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(text(query(container, ".bj-trail"))).toContain("1 flagged");
  });

  it("builds on press, keeps the brief already shown meanwhile and busies only its button", async () => {
    on(`GET ${PATH}`, { brief: BRIEF });
    let finish: (value: unknown) => void = () => undefined;
    on(`POST ${PATH}`, () => new Promise((resolve) => (finish = resolve)));
    const { container } = panel();
    await settle();
    const button = buttonNamed(container, "Refresh");
    button.focus();
    click(button);
    await settle(1);
    expect(button.textContent).toBe("Building...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(button);
    expect(container.querySelector(".bj-rs-brief")).not.toBeNull();
    click(button);
    expect(callsTo(`POST ${PATH}`)).toHaveLength(1);
    finish({ brief: { ...BRIEF, lead_with: "A new lead.", created_at: "2026-09-22T09:00:00Z" } });
    await settle();
    expect(text(query(container, ".bj-rs-part .bj-rs-text"))).toBe("A new lead.");
    expect(text(query(container, ".bj-rs-meta"))).toBe("Built Sep 22");
  });

  it("explains the server declining to build for want of evidence as a gold note with its own reason, keeps the shown brief and offers no Retry", async () => {
    on(`GET ${PATH}`, { brief: BRIEF });
    on(`POST ${PATH}`, new FakeApiError("There is no gap left to name for this role.", "INSUFFICIENT_EVIDENCE", undefined, 422));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Refresh"));
    await settle();
    const note = query(container, ".bj-rs-note");
    expect(text(note)).toBe("◆ There is no gap left to name for this role.");
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(container.querySelector(".bj-rs-brief")).not.toBeNull();
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
  });

  it("keeps the shown brief when a build fails and Retry builds again", async () => {
    on(`GET ${PATH}`, { brief: BRIEF });
    on(`POST ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Refresh"));
    await settle();
    expect(text(query(container, "[role='alert']"))).toContain("The server hit a problem.");
    expect(container.querySelector(".bj-rs-brief")).not.toBeNull();
    on(`POST ${PATH}`, { brief: BRIEF });
    const retry = buttonNamed(container, "Retry");
    retry.focus();
    click(retry);
    await settle();
    expect(container.querySelector("[role='alert']")).toBeNull();
    expect(callsTo(`POST ${PATH}`)).toHaveLength(2);
    expect(document.activeElement).toBe(buttonNamed(container, "Refresh"));
  });

  it("with no brief yet, a failed first build marks the block's own trail", async () => {
    on(`GET ${PATH}`, { brief: null });
    on(`POST ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Build my positioning brief"));
    await settle();
    expect(text(query(container, ".bj-trail"))).toBe("✕ That brief didn't finish");
  });

  it("links to what is missing, with no Retry and no trail, when a build needs a profile or a key", async () => {
    on(`GET ${PATH}`, { brief: null });
    on(`POST ${PATH}`, new FakeSetupError("Your play needs an active profile.", "/profile", "profile", ["profile_version"]));
    const { container } = panel();
    await settle();
    click(buttonNamed(container, "Build my positioning brief"));
    await settle();
    const link = query<HTMLAnchorElement>(container, ".bj-setup-notice a");
    expect(link.getAttribute("href")).toBe("/profile");
    expect(text(link)).toBe("Finish your profile");
    expect([...container.querySelectorAll("button")].some((button) => button.textContent === "Retry")).toBe(false);
    expect(container.querySelector(".bj-trail")).toBeNull();
  });

  it("names a read that failed with a Retry that reads again", async () => {
    on(`GET ${PATH}`, new FakeApiError("The server hit a problem.", "INTERNAL", undefined, 500));
    const { container } = panel();
    await settle();
    expect(text(query(container, ".bj-trail"))).toBe("✕ Your play couldn't be read");
    on(`GET ${PATH}`, { brief: BRIEF });
    const retry = buttonNamed(container, "Retry");
    retry.focus();
    click(retry);
    await settle();
    expect(container.querySelector(".bj-rs-brief")).not.toBeNull();
    expect(document.activeElement).toBe(buttonNamed(container, "Refresh"));
  });

  it("draws no cyan, and has no serious accessibility violation, empty or built", async () => {
    on(`GET ${PATH}`, { brief: { ...BRIEF, rubric_warnings: ["a flag"] } });
    const built = panel();
    await settle();
    expect(built.container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
    await expectNoSeriousA11yViolations(built.container);
    on(`GET ${PATH}`, { brief: null });
    const empty = panel();
    await settle();
    await expectNoSeriousA11yViolations(empty.container);
  });
});
