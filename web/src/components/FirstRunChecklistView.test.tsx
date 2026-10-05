import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  deriveFirstRun,
  type FirstRunFacts,
  type FirstRunFlags,
  type FirstRunView,
} from "../lib/firstRun";
import { expand, findAll, press, prop, textOf } from "../testing/reactTree";
import { FirstRunChecklistView } from "./FirstRunChecklistView";

// The card as a person meets it: a labelled region, a list, every step saying its state in
// words, and a real Dismiss button. What is done, and whether the card shows, is tested in
// firstRun.test.ts; this pins what the view does with a derived view.

const ok = <T,>(value: T) => ({ kind: "ok", value }) as const;
const failed = { kind: "failed" } as const;

const BRAND_NEW: FirstRunFacts = {
  profile: ok(false),
  modelKey: ok(false),
  savedSearches: ok(0),
  applications: ok({ count: 0, fromDiscover: 0, withResume: 0, resumeTargetId: null }),
};
const FLAGS: FirstRunFlags = { dismissed: false, searched: false };

function render(view: FirstRunView, onDismiss: () => void = () => {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <FirstRunChecklistView view={view} onDismiss={onDismiss} />
    </MemoryRouter>,
  );
}

function text(html: string): string {
  return html.replace(/<[^>]*>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
}

describe("FirstRunChecklistView", () => {
  it("is a labelled region holding an ordered list of the five steps", () => {
    const html = render(deriveFirstRun(BRAND_NEW, FLAGS));
    expect(html).toContain("<section");
    expect(html).toContain('aria-labelledby="bj-first-run-title"');
    expect(html).toContain('id="bj-first-run-title"');
    expect(html).toContain("<ol");
    expect((html.match(/<li/g) ?? []).length).toBe(5);
    expect(text(html)).toContain("0 of 5 done.");
    expect(text(html)).toContain("Next: Add your profile.");
  });

  it("links each step to the page where it is done", () => {
    const html = render(deriveFirstRun(BRAND_NEW, FLAGS));
    for (const [href, label] of [
      ["/profile", "Add your profile"],
      ["/profile/integrations", "Add a model key"],
      ["/discover", "Run a first search"],
      ["/applications", "Track a job"],
      ["/applications", "Generate a resume"],
    ]) {
      expect(html).toMatch(new RegExp(`<a href="${href}"[^>]*>${label}</a>`));
    }
  });

  it("says in words that a step is done, and marks only the first open one as next", () => {
    const facts: FirstRunFacts = { ...BRAND_NEW, profile: ok(true) };
    const html = render(deriveFirstRun(facts, FLAGS));
    expect(html).toContain('data-status="done"');
    expect(html).toContain(">Done</span>");
    expect(text(html)).toContain("1 of 5 done.");
    expect((html.match(/>Next</g) ?? []).length).toBe(1);
    expect(text(html)).toContain("Next: Add a model key.");
    // The other open steps are announced as to do, without being drawn.
    expect((html.match(/bj-visually-hidden">To do</g) ?? []).length).toBe(3);
  });

  it("labels an input that could not be loaded as such, not as done and not as to do", () => {
    const facts: FirstRunFacts = { ...BRAND_NEW, profile: failed };
    const html = render(deriveFirstRun(facts, FLAGS));
    expect(html).toContain('data-status="unknown"');
    expect(text(html)).toContain("Can't check right now");
    // A step that could not be checked is never named as the next one: the guide moves on
    // to the first step known to be left.
    expect(text(html)).toContain("Next: Add a model key.");
    expect(text(html)).not.toContain("Next: Add your profile.");
  });

  it("hides the tick glyph from a screen reader (the words carry the state)", () => {
    const html = render(deriveFirstRun({ ...BRAND_NEW, profile: ok(true) }, FLAGS));
    expect(html).toContain('class="bj-first-run-mark" aria-hidden="true"');
  });

  describe("the glyph and the badge each step shows", () => {
    // A done step, an unknown one, a todo one that is next, and one more todo that is not.
    const facts: FirstRunFacts = {
      profile: ok(true),
      modelKey: failed,
      savedSearches: ok(0),
      applications: ok({ count: 0, fromDiscover: 0, withResume: 0, resumeTargetId: null }),
    };
    const view = deriveFirstRun(facts, FLAGS);
    const items = findAll(expand(FirstRunChecklistView({ view, onDismiss: () => {} })), (el) => el.type === "li");
    // One <li> per step, in the order of view.steps.
    const byId = (id: string) => items[view.steps.findIndex((s) => s.id === id)];
    const mark = (id: string) =>
      textOf(findAll(byId(id).children, (el) => el.type === "span" && prop(el, "className") === "bj-first-run-mark")[0]);
    const badge = (id: string) =>
      findAll(byId(id).children, (el) => el.type === "span" && String(prop(el, "className")).startsWith("bj-badge"));

    it("ticks a done step, with the emerald Done badge", () => {
      expect(mark("profile")).toBe("✓");
      expect(prop(badge("profile")[0], "className")).toBe("bj-badge-emerald");
      expect(textOf(badge("profile")[0])).toBe("Done");
    });

    it("marks an unknown step with a question mark and the muted badge", () => {
      expect(mark("model_key")).toBe("?");
      expect(prop(badge("model_key")[0], "className")).toBe("bj-badge-muted");
    });

    it("leaves a step that is next, and one that is not, as an open circle, gold only on the next", () => {
      expect(view.nextStep?.id).toBe("first_search");
      expect(mark("first_search")).toBe("○");
      expect(prop(badge("first_search")[0], "className")).toBe("bj-badge-gold");
      expect(mark("track_job")).toBe("○");
      expect(badge("track_job")).toHaveLength(0);
    });
  });

  it("renders nothing when the derived view says to hide it", () => {
    expect(render(deriveFirstRun(BRAND_NEW, { dismissed: true, searched: false }))).toBe("");
    const done: FirstRunFacts = {
      profile: ok(true),
      modelKey: ok(true),
      savedSearches: ok(1),
      applications: ok({ count: 1, fromDiscover: 0, withResume: 1, resumeTargetId: "a" }),
    };
    expect(render(deriveFirstRun(done, FLAGS))).toBe("");
    const loading: FirstRunFacts = {
      profile: { kind: "loading" },
      modelKey: { kind: "loading" },
      savedSearches: { kind: "loading" },
      applications: { kind: "loading" },
    };
    expect(render(deriveFirstRun(loading, FLAGS))).toBe("");
  });

  describe("the Dismiss button", () => {
    function dismissButton(onDismiss: () => void) {
      const nodes = expand(FirstRunChecklistView({ view: deriveFirstRun(BRAND_NEW, FLAGS), onDismiss }));
      const buttons = findAll(nodes, (el) => el.type === "button");
      expect(buttons).toHaveLength(1);
      return buttons[0];
    }

    it("is a real button with a name that says what it dismisses", () => {
      const button = dismissButton(() => {});
      expect(prop(button, "type")).toBe("button");
      expect(textOf(button)).toBe("Dismiss");
      expect(prop(button, "aria-label")).toBe("Dismiss the getting started checklist");
    });

    it("calls its handler once when pressed", () => {
      let calls = 0;
      press(dismissButton(() => calls++));
      expect(calls).toBe(1);
    });
  });
});
