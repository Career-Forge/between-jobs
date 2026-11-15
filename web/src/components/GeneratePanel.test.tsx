// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GenerationController } from "../lib/useGeneration";
import { COVER_LETTER_EXTRA, SEVERAL_MODEL_CALLS, spendNotice } from "../lib/spendNotice";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { describedText, text } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { GeneratePanel, type GeneratePanelProps } from "./GeneratePanel";

vi.mock("../lib/api", async () => (await import("../testing/fakeApi")).apiModule);

const RESULT = {
  run_id: "0123456789abcdef",
  resume: { artifact_id: "a", version_id: "v", media_type: "application/pdf", sha256: "f00dfeedbabe0123" },
  ats_attempts: [],
  final_score: null,
  score_scale: "ats_100",
  warnings: ['pinned item "Acme" got 1 of 3 bullets', "Engine caution: summary shortened", 'unsupported claim (unverifiable): "x" -- y'],
};

function controller(overrides: Partial<GenerationController> = {}): GenerationController {
  return {
    read: "done",
    state: { kind: "idle" },
    hasResume: false,
    resultUnreadable: false,
    hasCoverLetter: false,
    wantCoverLetter: false,
    setWantCoverLetter: vi.fn(),
    downloaded: false,
    downloading: null,
    downloadError: null,
    checklist: { kind: "idle" },
    generate: vi.fn().mockResolvedValue(undefined),
    downloadResume: vi.fn().mockResolvedValue(undefined),
    downloadCoverLetter: vi.fn().mockResolvedValue(undefined),
    loadChecklist: vi.fn().mockResolvedValue(undefined),
    retryRead: vi.fn(),
    ...overrides,
  };
}

function panel(c: GenerationController, props: Partial<GeneratePanelProps> = {}) {
  return render(
    <MemoryRouter>
      <GeneratePanel applicationId="a1" controller={c} {...props} />
    </MemoryRouter>,
  );
}

beforeEach(() => undefined);
afterEach(() => vi.restoreAllMocks());

describe("Generate", () => {
  it("says what it runs, with a secondary Generate resume and the cover letter choice off by default", () => {
    const c = controller();
    const { container } = panel(c);
    // The failed-run Retry runs the same press, so the sentence names it too.
    expect(text(query(container, ".bj-ws-intro"))).toContain(
      "Runs the resume engine against this job. Pressing Generate resume, or Retry after a failed run, makes several real model calls on your own key.",
    );
    expect(text(query(container, ".bj-ws-intro"))).toContain(
      spendNotice("Generate resume, or Retry after a failed run,", SEVERAL_MODEL_CALLS),
    );
    expect(text(container)).not.toContain("a real model call, on the provider key you saved");
    const run = buttonNamed(container, "Generate resume");
    expect(run.classList.contains("bj-primary")).toBe(false);
    click(run);
    expect(c.generate).toHaveBeenCalledWith();
    const box = query<HTMLInputElement>(container, "input[type='checkbox']");
    expect(box.checked).toBe(false);
    // A cover letter can take a second call when the first fails its checks, so the choice never says one.
    expect(text(box.closest("label")!)).toBe(`Also generate a cover letter (${COVER_LETTER_EXTRA})`);
    expect(text(box.closest("label")!)).toContain("a second if the letter fails its checks");
    click(box);
    expect(c.setWantCoverLetter).toHaveBeenCalledWith(true);
  });

  it("leaves the cover letter choice to the band when the band carries it", () => {
    const { container } = panel(controller(), { coverLetterInBand: true });
    expect(container.querySelector("input[type='checkbox']")).toBeNull();
  });

  it("shows a placeholder while the stored result is read, and no result", () => {
    const { container } = panel(controller({ read: "loading" }));
    expect(query(container, "[role='status'][aria-busy='true']").textContent).toContain("Loading your last generation...");
  });

  it("names a failed read with a Retry", () => {
    const c = controller({ read: "failed" });
    const { container } = panel(c);
    click(buttonNamed(container, "Retry"));
    expect(c.retryRead).toHaveBeenCalledTimes(1);
  });

  it("is busy while generating: the button reads Generating... with aria-disabled, and the checkbox is off limits", () => {
    const { container } = panel(controller({ state: { kind: "generating" } }));
    const run = buttonNamed(container, "Generating...");
    expect(run.getAttribute("aria-disabled")).toBe("true");
    expect(query<HTMLInputElement>(container, "input[type='checkbox']").disabled).toBe(true);
    expect(text(query(container, "[role='status']"))).toBe("Generating...");
  });

  it("draws a failed run as a callout with a Retry, unless the band carries Generate again", () => {
    const c = controller({ state: { kind: "error", message: "The engine did not answer.", at: "09:14" } });
    const own = panel(c);
    click(buttonNamed(own.container, "Retry"));
    expect(c.generate).toHaveBeenCalledTimes(1);
    own.unmount();
    const inBand = panel(c, { retryInBand: true });
    expect(text(query(inBand.container, "[role='alert']"))).toContain("The engine did not answer.");
    expect(inBand.container.querySelector("[role='alert'] button")).toBeNull();
  });

  // The sentence is in the intro, which stays on screen in every state: a Retry after a failed run is a paid press,
  // and a person who has not set up a model key yet is told what the press will cost once they have.
  it("keeps the spend sentence in the intro after a failed run, naming Retry, and without Retry when the band carries Generate again", () => {
    const failed = controller({ state: { kind: "error", message: "The engine did not answer.", at: "09:14" } });
    const own = panel(failed);
    expect(text(query(own.container, ".bj-ws-intro"))).toContain(
      "Pressing Generate resume, or Retry after a failed run, makes several real model calls on your own key.",
    );
    own.unmount();
    const inBand = panel(failed, { retryInBand: true });
    expect(text(query(inBand.container, ".bj-ws-intro"))).toContain(spendNotice("Generate resume", SEVERAL_MODEL_CALLS));
    expect(text(query(inBand.container, ".bj-ws-intro"))).not.toContain("Retry");
  });

  it("keeps the spend sentence in the intro while setup is missing, although no Generate button is drawn yet", () => {
    const { container } = panel(controller(), { setupMissing: { message: "No usable key.", missing: [{ label: "Add a model key", to: "/settings#ai-provider" }] } });
    expect(container.querySelector(".bj-actions")).toBeNull();
    expect(text(query(container, ".bj-ws-intro"))).toContain("makes several real model calls on your own key.");
  });

  it("keeps the link in a setup failure, and does not fire anything", () => {
    const { container } = panel(controller({ state: { kind: "setup", notice: { message: "Add a model key.", linkTo: "/settings", linkLabel: "Add a model key in Settings" } } }));
    expect(query(container, ".bj-setup-notice a").getAttribute("href")).toBe("/settings");
  });

  it("with known missing setup shows what is missing with its link, and no Generate button", () => {
    const { container } = panel(controller(), { setupMissing: { message: "No usable key.", missing: [{ label: "Add a model key", to: "/settings#ai-provider" }] } });
    expect(query(container, ".bj-state--setup a").getAttribute("href")).toBe("/settings#ai-provider");
    expect(container.querySelector(".bj-actions")).toBeNull();
  });

  it("draws a drafted result with its trail, cautions as neutral callouts, the pin conflict on its own, and no claim warning (that is Review's)", () => {
    const { container } = panel(controller({ hasResume: true, state: { kind: "ready", restored: false, result: RESULT } }));
    expect(text(container)).toContain("Resume drafted");
    expect(text(query(container, ".bj-ws-cautions"))).toBe("Engine caution: summary shortened");
    expect(text(container)).toContain("Some mandatory entries didn't fully fit:");
    expect(text(container)).not.toContain("unsupported claim");
    expect(text(query(container, ".bj-trail"))).toContain("Drafted by your model · on your model key · 1 flagged");
  });

  it("offers the downloads of a resume it has, and the cover letter only when the result made one", () => {
    const c = controller({ hasResume: true, hasCoverLetter: true, state: { kind: "ready", restored: false, result: { ...RESULT, cover_letter: RESULT.resume } } });
    const { container } = panel(c);
    click(buttonNamed(container, "Download PDF"));
    click(buttonNamed(container, "Download cover letter"));
    expect(c.downloadResume).toHaveBeenCalledTimes(1);
    expect(c.downloadCoverLetter).toHaveBeenCalledTimes(1);
  });

  it("shows Compiling PDF... on a download in progress and ignores a second press", () => {
    const c = controller({ hasResume: true, downloading: "resume" });
    const { container } = panel(c);
    const busy = buttonNamed(container, "Compiling PDF...");
    expect(busy.getAttribute("aria-disabled")).toBe("true");
    click(busy);
    expect(c.downloadResume).not.toHaveBeenCalled();
  });

  it("shows a download failure as an alert", () => {
    const { container } = panel(controller({ hasResume: true, downloadError: "Failed to download" }));
    expect(text(query(container, "[role='alert']"))).toBe("Failed to download");
  });

  it("says a result with no resume and no fit produced nothing", () => {
    const { container } = panel(controller({ state: { kind: "ready", restored: true, result: { ...RESULT, resume: null } } }));
    expect(text(container)).toContain("The engine didn't produce a resume for this job.");
  });

  it("passes an accessibility check", async () => {
    const { container } = panel(controller({ hasResume: true, state: { kind: "ready", restored: true, result: RESULT } }));
    await expectNoSeriousA11yViolations(container);
  });
});

describe("Generate, described by what pressing it costs", () => {
  it("describes the Generate resume button by the sentence in its intro, naming Retry", () => {
    const { container } = panel(controller());
    const button = buttonNamed(container, "Generate resume");
    expect(describedText(button)).toBe(spendNotice("Generate resume, or Retry after a failed run,", SEVERAL_MODEL_CALLS));
    expect(query(container, ".bj-ws-intro").contains(document.getElementById(button.getAttribute("aria-describedby")!))).toBe(true);
  });

  it("describes it by the sentence that does not name Retry when the band carries Generate again", () => {
    const { container } = panel(controller(), { retryInBand: true });
    expect(describedText(buttonNamed(container, "Generate resume"))).toBe(spendNotice("Generate resume", SEVERAL_MODEL_CALLS));
  });

  it("keeps the description on the button that reads Generating... while a run is out, and on the one after a failed run", () => {
    const busy = panel(controller({ state: { kind: "generating" } }));
    expect(describedText(buttonNamed(busy.container, "Generating..."))).toContain("Pressing Generate resume");
    busy.unmount();
    const failed = panel(controller({ state: { kind: "error", message: "The engine did not answer.", at: "09:14" } }));
    expect(describedText(buttonNamed(failed.container, "Generate resume"))).toContain("Pressing Generate resume");
  });

  it("changes none of the intro's words: the note is the same text, in the same place", () => {
    const { container } = panel(controller());
    expect(text(query(container, ".bj-ws-intro"))).toContain(
      "Runs the resume engine against this job. Pressing Generate resume, or Retry after a failed run, makes several real model calls on your own key.",
    );
  });

  it("draws no button, so names no id, while setup is missing", () => {
    const { container } = panel(controller(), { setupMissing: { message: "No usable key.", missing: [{ label: "Add a model key", to: "/settings#ai-provider" }] } });
    expect(container.querySelector("[aria-describedby]")).toBeNull();
  });

  it("gives each panel on the page its own note, so two never share an id", () => {
    const first = panel(controller());
    const second = panel(controller());
    const a = buttonNamed(first.container, "Generate resume").getAttribute("aria-describedby");
    const b = buttonNamed(second.container, "Generate resume").getAttribute("aria-describedby");
    expect(a).not.toBe(b);
    expect(first.container.contains(document.getElementById(a!))).toBe(true);
    expect(second.container.contains(document.getElementById(b!))).toBe(true);
  });
});
