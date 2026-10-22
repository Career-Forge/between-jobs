// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { TOUR, TOUR_TABS } from "../../content/tour";
import { FORBIDDEN } from "../../testing/claims";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { click, keydown, query, render } from "../../testing/render";
import { ProductTour } from "./ProductTour";

// The tour as a person meets it: a tablist that is one Tab stop, five tabs that each control their own panel,
// one panel showing at a time, a sample next step that rewrites for the tab in view, and drawings that are
// pictures and never controls.

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const tabsOf = (root: ParentNode) => [...root.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
const panelsOf = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('[role="tabpanel"]')];
const visiblePanel = (root: ParentNode) => panelsOf(root).filter((panel) => !panel.hidden)[0];
const selected = (root: ParentNode) => tabsOf(root).filter((tab) => tab.getAttribute("aria-selected") === "true");

function open() {
  return render(<ProductTour />).container;
}

describe("the tab strip", () => {
  it("is a tablist named for what it holds, with the five tabs in order, numbered 01 to 05", () => {
    const root = open();
    expect(query(root, '[role="tablist"]').getAttribute("aria-label")).toBe(TOUR.tablistLabel);
    expect(tabsOf(root).map((tab) => text(tab))).toEqual(TOUR_TABS.map((tab, index) => `0${index + 1}${tab.label}`));
    expect(TOUR_TABS.map((tab) => tab.label)).toEqual(["Discover", "Prepare", "Reach out", "Practice", "Apply"]);
    // The number is a mark, read as nothing: the tab's name is its label.
    for (const tab of tabsOf(root)) expect(tab.querySelector(".bj-tour-tab-number")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("starts on Discover, with exactly one tab selected and the selection style on it (aria-selected, never an underline)", () => {
    const root = open();
    expect(selected(root).map((tab) => text(tab))).toEqual(["01Discover"]);
    for (const tab of tabsOf(root)) {
      expect(tab.classList.contains("bj-selectable")).toBe(true);
      expect(tab.type).toBe("button");
    }
  });

  it("is one Tab stop: the selected tab is tabbable and the rest are not (a roving tabindex)", () => {
    const root = open();
    expect(tabsOf(root).map((tab) => tab.tabIndex)).toEqual([0, -1, -1, -1, -1]);
    click(tabsOf(root)[2]);
    expect(tabsOf(root).map((tab) => tab.tabIndex)).toEqual([-1, -1, 0, -1, -1]);
  });

  it("gives every tab its own panel, which it controls and which is labelled by it", () => {
    const root = open();
    const panels = panelsOf(root);
    expect(panels).toHaveLength(TOUR_TABS.length);
    tabsOf(root).forEach((tab, index) => {
      expect(tab.getAttribute("aria-controls")).toBe(panels[index].id);
      expect(panels[index].getAttribute("aria-labelledby")).toBe(tab.id);
    });
    // The ids are unique.
    expect(new Set([...tabsOf(root), ...panels].map((element) => element.id)).size).toBe(TOUR_TABS.length * 2);
  });

  it("shows one panel at a time, and draws nothing in the other four", () => {
    const root = open();
    expect(panelsOf(root).filter((panel) => !panel.hidden)).toHaveLength(1);
    expect(panelsOf(root).filter((panel) => panel.hidden).every((panel) => panel.childElementCount === 0)).toBe(true);
    expect(visiblePanel(root)).toBe(panelsOf(root)[0]);
    click(tabsOf(root)[3]);
    expect(visiblePanel(root)).toBe(panelsOf(root)[3]);
    expect(panelsOf(root).filter((panel) => !panel.hidden)).toHaveLength(1);
    expect(panelsOf(root)[0].childElementCount).toBe(0);
  });

  it("makes the panel a stop of its own (it holds no control), so a keyboard reader can scroll it", () => {
    const root = open();
    expect(visiblePanel(root).tabIndex).toBe(0);
  });
});

describe("the keys", () => {
  it("moves to the next tab with Right and the previous with Left, wrapping at both ends, and selects it", () => {
    const root = open();
    tabsOf(root)[0].focus();
    keydown("ArrowRight");
    expect(document.activeElement).toBe(tabsOf(root)[1]);
    expect(selected(root)).toEqual([tabsOf(root)[1]]);
    keydown("ArrowLeft");
    keydown("ArrowLeft");
    expect(document.activeElement).toBe(tabsOf(root)[4]);
    expect(selected(root)).toEqual([tabsOf(root)[4]]);
    keydown("ArrowRight");
    expect(document.activeElement).toBe(tabsOf(root)[0]);
  });

  it("goes to the first tab with Home and the last with End", () => {
    const root = open();
    tabsOf(root)[2].focus();
    keydown("End");
    expect(document.activeElement).toBe(tabsOf(root)[4]);
    expect(visiblePanel(root)).toBe(panelsOf(root)[4]);
    keydown("Home");
    expect(document.activeElement).toBe(tabsOf(root)[0]);
    expect(visiblePanel(root)).toBe(panelsOf(root)[0]);
  });

  it("keeps the keys it uses from also scrolling the page, and leaves every other key alone", () => {
    const root = open();
    tabsOf(root)[0].focus();
    for (const key of ["ArrowRight", "ArrowLeft", "Home", "End"]) expect(keydown(key), key).toBe(false);
    for (const key of ["ArrowDown", "ArrowUp", "a", "Enter", " ", "Tab", "Escape"]) expect(keydown(key), key).toBe(true);
    expect(selected(root)).toHaveLength(1);
  });
});

describe("the keys with a modifier", () => {
  // Alt+Left and Cmd+Left are the browser's Back, Cmd+Right is Forward, Ctrl+Home and Cmd+End move through the
  // page, and Shift+Arrow is a selection: after a click a tab holds focus, so the strip must not eat them.
  const chords: [string, string, { shift?: boolean; alt?: boolean; ctrl?: boolean; meta?: boolean }][] = [
    ["Alt+ArrowLeft", "ArrowLeft", { alt: true }],
    ["Alt+ArrowRight", "ArrowRight", { alt: true }],
    ["Cmd+ArrowLeft", "ArrowLeft", { meta: true }],
    ["Cmd+ArrowRight", "ArrowRight", { meta: true }],
    ["Ctrl+Home", "Home", { ctrl: true }],
    ["Cmd+End", "End", { meta: true }],
    ["Shift+ArrowRight", "ArrowRight", { shift: true }],
  ];

  it.each(chords)("leaves %s to the browser: it is not prevented and the selection stays", (_name, key, modifiers) => {
    const root = open();
    click(tabsOf(root)[2]);
    tabsOf(root)[2].focus();
    expect(keydown(key, modifiers)).toBe(true);
    expect(selected(root)).toEqual([tabsOf(root)[2]]);
    expect(document.activeElement).toBe(tabsOf(root)[2]);
  });
});

describe("the sample next step", () => {
  it("is a polite, atomic live region under the tabs, named as a sample", () => {
    const root = open();
    const live = query(root, '[role="status"]');
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.getAttribute("aria-atomic")).toBe("true");
    expect(query(root, 'section[aria-label="A sample next step"]')).not.toBeNull();
    // It is not the page's band: there is one of those, in the hero.
    expect(root.querySelector(".bj-band")).toBeNull();
  });

  it("rewrites for each tab: the sentence and the why line, with the tab's name read out first", () => {
    const root = open();
    TOUR_TABS.forEach((tab, index) => {
      click(tabsOf(root)[index]);
      const live = query(root, '[role="status"]');
      expect(text(query(live, ".bj-tour-next-sentence"))).toBe(tab.next.sentence);
      expect(text(query(live, ".bj-tour-next-why"))).toBe(`why: ${tab.next.why.join(" · ")}`);
      expect(text(query(live, ".bj-visually-hidden"))).toBe(`${tab.label}, screen ${index + 1} of ${TOUR_TABS.length}.`);
      expect(text(live).startsWith(`${tab.label}, screen ${index + 1}`)).toBe(true);
    });
  });

  it("has no button and no cyan fill: the hero holds the page's one", () => {
    const root = open();
    for (let index = 0; index < TOUR_TABS.length; index++) {
      click(tabsOf(root)[index]);
      expect(root.querySelector(".bj-primary, .bj-button-link-primary, .bj-band-action")).toBeNull();
      expect(query(root, ".bj-tour-next").querySelector("button, a, input")).toBeNull();
    }
  });
});

describe("each drawing", () => {
  it.each(TOUR_TABS.map((tab, index) => [tab.label, index] as const))("%s is a picture: nothing in it takes focus or fetches, and it ends in its trail line", (_name, index) => {
    const root = open();
    click(tabsOf(root)[index]);
    const panel = visiblePanel(root);
    expect(panel.querySelector("button, a, input, select, textarea, summary, [tabindex], img, svg, form")).toBeNull();
    expect(panel.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
    const last = panel.querySelector(".bj-tour-grid")?.lastElementChild;
    expect(last?.classList.contains("bj-trail")).toBe(true);
    // Who made it, what it is based on, what nobody checked.
    expect(text(last as Element)).toMatch(/not checked: /);
    expect(text(last as Element)).not.toMatch(/verified/i);
    expect(panel.querySelectorAll(".bj-trail")).toHaveLength(1);
  });

  it("says nothing the home page's claim list forbids, and carries no digit, in any of the five", () => {
    const root = open();
    TOUR_TABS.forEach((_tab, index) => {
      click(tabsOf(root)[index]);
      const words = text(visiblePanel(root));
      for (const [name, pattern] of FORBIDDEN) expect(words, name).not.toMatch(pattern);
      // The checklist's own count of its three rows, "(3)", is the one number: it is a count of what is drawn.
      expect(words.replace(/\(3\)/, ""), "digits").not.toMatch(/\d/);
    });
  });

  it("Discover: the role card, the fit block with its parts, and the skills table with what is and is not in the profile", () => {
    const root = open();
    const panel = visiblePanel(root);
    expect(text(panel)).toContain(TOUR.role);
    expect(text(panel)).toContain(TOUR.company);
    expect(query(panel, '[role="img"]').getAttribute("aria-label")).toBe("Fit: Good");
    expect(text(panel)).toContain("Lowest part: Skills");
    const rows = [...panel.querySelectorAll("tbody tr")].map((row) => text(row));
    expect(rows).toEqual([
      "Python◆ In your profile",
      "Evals◆ In your profile",
      "Retrieval◆ In your profile",
      "Kubernetes○ Not in your profile",
    ]);
    expect(panel.querySelectorAll("table caption")).toHaveLength(1);
    // The bars are a picture; the words beside them carry the meaning.
    expect([...panel.querySelectorAll(".bj-tour-parts li")].map((item) => text(item))).toEqual(["SkillsMixed", "ExperienceGood", "LocationStrong"]);
    expect(panel.querySelectorAll(".bj-segbar-cells")).toHaveLength(3);
    expect([...panel.querySelectorAll(".bj-segbar-cells")].every((cells) => cells.getAttribute("aria-hidden") === "true")).toBe(true);
  });

  it("Prepare: a resume page with the flagged word marked, the flagged-claim row, and the three checks that are not checked", () => {
    const root = open();
    click(tabsOf(root)[1]);
    const panel = visiblePanel(root);
    expect(text(query(panel, "mark"))).toBe("Led");
    expect(text(query(panel, ".bj-tour-sheet"))).toContain("(Marked: flagged by the claim check.)");
    expect(text(query(panel, ".bj-claimrow"))).toContain("Unverifiable");
    expect(text(query(panel, ".bj-claimrow"))).toContain(TOUR.prepare.claim);
    const checks = [...panel.querySelectorAll(".bj-checkrow")];
    expect(checks).toHaveLength(3);
    for (const check of checks) expect(text(check)).toContain("Not checked");
    expect(checks.map((check) => text(query(check, ".bj-checkrow-label")))).toEqual(["Links are valid", "Fonts are embedded", "No duplicate bullets"]);
    // Nothing is ticked, and nothing can be: a row is a statement.
    expect(panel.querySelector("input, button")).toBeNull();
    // The page is paper, a group named for what it is.
    expect(query(panel, ".bj-tour-sheet").getAttribute("aria-label")).toBe(TOUR.prepare.pageLabel);
  });

  it("Reach out: a draft that was not sent, and the one sentence that says who presses Send", () => {
    const root = open();
    click(tabsOf(root)[2]);
    const panel = visiblePanel(root);
    expect(text(panel)).toContain("Not sent");
    expect(text(panel)).toContain(TOUR.reach.subject);
    expect(text(query(panel, ".bj-tour-statement"))).toBe("Waiting in your drafts. You press Send.");
    expect(text(panel)).toContain("optional");
    expect(text(panel)).toContain("invited people");
  });

  it("Practice: a question, the written answer, the four parts judged by a model, and written feedback", () => {
    const root = open();
    click(tabsOf(root)[3]);
    const panel = visiblePanel(root);
    expect(text(query(panel, ".bj-tour-question"))).toBe(TOUR.practice.question);
    expect(text(panel)).toContain(TOUR.practice.answer);
    const parts = [...panel.querySelectorAll(".bj-tour-star li")].map((item) => text(item));
    expect(parts).toEqual(["Situation: found", "Task: found", "Action: found", "Result: not found"]);
    expect(panel.querySelector(".bj-tour-star")?.getAttribute("aria-label")).toBe("Judged by your model");
    expect(text(panel)).toContain("Not a prediction of how an interview will go.");
  });

  it("Apply: a form with filled fields and fields left for the person, the side panel's depicted button, and where it stops", () => {
    const root = open();
    click(tabsOf(root)[4]);
    const panel = visiblePanel(root);
    const fields = [...panel.querySelectorAll(".bj-tour-fields li")].map((item) => text(item));
    expect(fields).toEqual([
      "Full nameFilled",
      "EmailFilled",
      "ResumeFilled",
      "Demographic questionsLeft for you",
      "Accommodation questionsLeft for you",
    ]);
    // The depicted buttons are outlined spans hidden from assistive technology, never controls.
    const mocks = [...panel.querySelectorAll(".bj-tour-mock-button")];
    expect(mocks.map((mock) => text(mock))).toEqual(["Submit", "Fill this page"]);
    for (const mock of mocks) {
      expect(mock.tagName).toBe("SPAN");
      expect(mock.getAttribute("aria-hidden")).toBe("true");
    }
    expect(text(panel)).toContain("The side panel shows a Fill this page button.");
    expect(text(panel)).toContain("Stops here. It never submits.");
    expect(text(panel)).toContain("The extension is for early testers and is not in the Chrome Web Store yet.");
  });
});

describe("accessibility", () => {
  it.each(TOUR_TABS.map((tab, index) => [tab.label, index] as const))("has no serious or critical axe violation on the %s tab", async (_name, index) => {
    const root = open();
    click(tabsOf(root)[index]);
    await expectNoSeriousA11yViolations(root);
  });

  it("has no heading of its own except the checklist's inside Prepare, so the page's heading order is the page's", () => {
    const root = open();
    expect(root.querySelector("h1, h2, h3, h4")).toBeNull();
    click(tabsOf(root)[1]);
    expect([...root.querySelectorAll("h1, h2, h3, h4")].map((heading) => heading.tagName)).toEqual(["H3"]);
  });
});
