// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LANDING } from "../../content/landing";
import { BOARD_STATUSES, STAGES } from "../../content/landingCircuit";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { click, keydown, query, render } from "../../testing/render";
import { CircuitDemo } from "./CircuitDemo";

// The circuit as a person meets it: five stage buttons that are really buttons, their pressed state, the
// card each one lights, the sample next step that rewrites and is announced politely, the keys that
// move between the stages, and the promise that nothing in it is a control but the five buttons.

const circuit = () => render(<CircuitDemo />).container;
const stageButtons = (root: ParentNode) => [...root.querySelectorAll<HTMLButtonElement>("button.bj-circuit-stage-button")];
const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();

describe("the stage buttons", () => {
  it("are five real buttons in an ordered list, named by their stage, each with aria-pressed", () => {
    const root = circuit();
    const buttons = stageButtons(root);
    expect(buttons.map((button) => text(button.querySelector(".bj-circuit-stage-label") as Element))).toEqual(STAGES.map((stage) => stage.label));
    for (const button of buttons) {
      expect(button.tagName).toBe("BUTTON");
      expect(button.getAttribute("type")).toBe("button");
      expect(["true", "false"]).toContain(button.getAttribute("aria-pressed"));
      // The selection primitive: the ARIA state is what turns the maroon fill on.
      expect(button.classList.contains("bj-selectable")).toBe(true);
    }
    // A real group, named, so a screen reader announces the set the five toggles belong to.
    const group = query(root, '[role="group"]');
    expect(group.getAttribute("aria-label")).toBe(LANDING.circuit.stagesLabel);
    const list = query(group, "ol.bj-circuit-stages");
    expect(list.querySelectorAll(":scope > li")).toHaveLength(STAGES.length);
    // The numeral is decoration; the label is the name.
    for (const node of root.querySelectorAll(".bj-circuit-node")) expect(node.getAttribute("aria-hidden")).toBe("true");
  });

  it("start with exactly one stage pressed, and a click moves the press to the stage clicked", () => {
    const root = circuit();
    const pressed = () => stageButtons(root).map((button) => button.getAttribute("aria-pressed"));
    expect(pressed().filter((value) => value === "true")).toHaveLength(1);
    expect(pressed()[1]).toBe("true");
    click(stageButtons(root)[3]);
    expect(pressed()).toEqual(["false", "false", "false", "true", "false"]);
    click(stageButtons(root)[0]);
    expect(pressed()).toEqual(["true", "false", "false", "false", "false"]);
    // Pressing the one that is already pressed keeps it pressed: a stage is picked, not toggled off.
    click(stageButtons(root)[0]);
    expect(pressed()[0]).toBe("true");
  });

  it("keep the pressed button where it was on screen when an earlier, taller card folds away above it", () => {
    const root = circuit();
    const scrollBy = vi.spyOn(window, "scrollBy").mockImplementation(() => undefined);
    const target = stageButtons(root)[3];
    // The button is at 373 when pressed and at 71 once the card above it has folded (a phone).
    const tops = [373, 71];
    vi.spyOn(target, "getBoundingClientRect").mockImplementation(() => ({ top: tops.shift() ?? 71 }) as DOMRect);
    click(target);
    expect(scrollBy).toHaveBeenCalledTimes(1);
    expect(scrollBy).toHaveBeenCalledWith(0, -302);
    // A press that moves nothing (the row layout, or the stage already in view) scrolls nothing.
    scrollBy.mockClear();
    click(stageButtons(root)[0]);
    expect(scrollBy).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it("mark the stage in view on its list item, which is what the phone layout shows a card for", () => {
    const root = circuit();
    const active = () => [...root.querySelectorAll(".bj-circuit-stage")].map((item) => item.getAttribute("data-active"));
    expect(active()).toEqual(["false", "true", "false", "false", "false"]);
    click(stageButtons(root)[4]);
    expect(active()).toEqual(["false", "false", "false", "false", "true"]);
  });

  it("point at their cards with aria-controls, and every card exists", () => {
    const root = circuit();
    for (const button of stageButtons(root)) {
      const id = button.getAttribute("aria-controls") ?? "";
      expect(root.querySelector(`#${CSS.escape(id)}`)?.classList.contains("bj-circuit-card")).toBe(true);
    }
  });
});

describe("the keyboard", () => {
  it("moves focus along the row with Left and Right and with Home and End, without wrapping at the ends", () => {
    const root = circuit();
    const buttons = stageButtons(root);
    buttons[0].focus();
    expect(keydown("ArrowLeft")).toBe(true);
    expect(document.activeElement).toBe(buttons[0]);
    expect(keydown("ArrowRight")).toBe(false);
    expect(document.activeElement).toBe(buttons[1]);
    keydown("ArrowRight");
    expect(document.activeElement).toBe(buttons[2]);
    keydown("ArrowLeft");
    expect(document.activeElement).toBe(buttons[1]);
    keydown("End");
    expect(document.activeElement).toBe(buttons[4]);
    expect(keydown("ArrowRight")).toBe(true);
    expect(document.activeElement).toBe(buttons[4]);
    keydown("Home");
    expect(document.activeElement).toBe(buttons[0]);
  });

  it("leaves Up and Down to the page: in a row they have nothing to move along, and they scroll", () => {
    const root = circuit();
    const buttons = stageButtons(root);
    buttons[2].focus();
    expect(keydown("ArrowDown")).toBe(true);
    expect(keydown("ArrowUp")).toBe(true);
    expect(document.activeElement).toBe(buttons[2]);
  });

  describe("under 960px, where the stages are a vertical list", () => {
    const original = window.matchMedia;
    beforeEach(() => {
      window.matchMedia = ((query: string) => ({ matches: false, media: query })) as unknown as typeof window.matchMedia;
    });
    afterEach(() => {
      window.matchMedia = original;
    });

    it("takes no key at all, so arrows and Home and End scroll the page and Tab walks the buttons", () => {
      const root = circuit();
      const buttons = stageButtons(root);
      buttons[4].focus();
      for (const key of ["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"]) {
        expect(keydown(key), key).toBe(true);
        expect(document.activeElement).toBe(buttons[4]);
      }
    });
  });

  it("only moves focus: moving along the row never picks a stage or rewrites the sample step", () => {
    const root = circuit();
    const before = text(query(root, ".bj-circuit-demo-body"));
    stageButtons(root)[0].focus();
    keydown("ArrowRight");
    keydown("ArrowRight");
    expect(stageButtons(root)[1].getAttribute("aria-pressed")).toBe("true");
    expect(text(query(root, ".bj-circuit-demo-body"))).toBe(before);
  });

  it("leaves every other key alone, so Tab, Enter and Space keep their ordinary meaning", () => {
    const root = circuit();
    stageButtons(root)[0].focus();
    for (const key of ["Tab", "Enter", " ", "a"]) expect(keydown(key), key).toBe(true);
    expect(document.activeElement).toBe(stageButtons(root)[0]);
  });

  it("has no other tab stop: the five stage buttons are the only focusable things in the circuit", () => {
    const root = circuit();
    const focusable = root.querySelectorAll('a, button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])');
    expect([...focusable]).toEqual(stageButtons(root));
  });
});

describe("the sample next step", () => {
  it("is a polite, atomic live region, so a stage change is announced once and whole", () => {
    const root = circuit();
    const live = query(root, ".bj-circuit-demo-body");
    expect(live.getAttribute("role")).toBe("status");
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.getAttribute("aria-atomic")).toBe("true");
  });

  it("rewrites for each stage: its sentence, its why line, and which stage it is, said first for a screen reader", () => {
    const root = circuit();
    const live = query(root, ".bj-circuit-demo-body");
    STAGES.forEach((stage, index) => {
      click(stageButtons(root)[index]);
      const words = text(live);
      expect(words).toContain(`Stage ${index + 1} of ${STAGES.length}, ${stage.label}.`);
      expect(words.indexOf("Stage")).toBeLessThan(words.indexOf(stage.demo.sentence));
      expect(text(query(live, ".bj-circuit-demo-sentence"))).toBe(stage.demo.sentence);
      expect(text(query(live, ".bj-circuit-demo-why"))).toBe(`why: ${stage.demo.why.join(" · ")}`);
    });
  });

  it("is labeled a sample, in the region and above it", () => {
    const root = circuit();
    expect(query(root, "section.bj-circuit-demo").getAttribute("aria-label")).toBe(LANDING.circuit.demoLabel);
    expect(text(query(root, ".bj-circuit-demo-tag"))).toBe(LANDING.circuit.demoLabel);
  });

  it("draws a stage's action as an outlined picture the reader is told about, never as a control", () => {
    const root = circuit();
    for (const [index, stage] of STAGES.entries()) {
      click(stageButtons(root)[index]);
      const mocks = [...root.querySelectorAll(".bj-circuit-demo-action .bj-circuit-mock")];
      if (stage.demo.action === null) {
        expect(mocks).toHaveLength(0);
        expect(text(query(root, ".bj-circuit-demo-body"))).not.toContain("button");
      } else {
        expect(mocks).toHaveLength(1);
        expect(text(mocks[0])).toBe(stage.demo.action);
        expect(mocks[0].tagName).toBe("SPAN");
        expect(mocks[0].getAttribute("aria-hidden")).toBe("true");
        expect(text(query(root, ".bj-circuit-demo-body"))).toContain(`The sample shows a ${stage.demo.action} button.`);
      }
    }
  });
});

describe("the cards", () => {
  it("are built from the fictional sample role and say so, and carry no date, no count and no statistic", () => {
    const root = circuit();
    expect(text(query(root, ".bj-circuit-sample-label"))).toBe(LANDING.circuit.sampleLabel);
    expect(LANDING.circuit.sampleLabel).toContain("fictional");
    const cards = [...root.querySelectorAll(".bj-circuit-card")];
    expect(cards).toHaveLength(STAGES.length);
    for (const card of cards) {
      expect(text(card)).not.toMatch(/\d/);
      expect(text(card)).not.toMatch(/%|\bpercent\b|\bscore\b/i);
    }
  });

  it("each end in a trail line, in the grammar: a plain 12px line of words, never a button", () => {
    const root = circuit();
    for (const card of root.querySelectorAll(".bj-circuit-card")) {
      const trails = card.querySelectorAll("p.bj-trail");
      expect(trails).toHaveLength(1);
      expect(card.lastElementChild).toBe(trails[0]);
      expect(trails[0].querySelector("button")).toBeNull();
      expect(text(trails[0]).length).toBeGreaterThan(10);
      // Every glyph is decoration; the words carry the meaning.
      for (const glyph of trails[0].querySelectorAll(".bj-trail-glyph")) expect(glyph.getAttribute("aria-hidden")).toBe("true");
    }
  });

  it("say who acted in each stage: a model on your key where one wrote it, plain code where none did", () => {
    const root = circuit();
    const trails = [...root.querySelectorAll("p.bj-trail")].map(text);
    expect(trails[0]).toContain("engine");
    expect(trails[0]).toContain("code-weighted from your model's sub-scores");
    expect(trails[1]).toContain("Checked by code");
    expect(trails[1]).toContain("no model involved");
    expect(trails[2]).toContain("Drafted by your model");
    expect(trails[2]).toContain("never sent by Between Jobs");
    expect(trails[3]).toContain("Scored by your model");
    expect(trails[4]).toContain("Filled by code");
    expect(trails[4]).toContain("submit is yours");
  });

  it("show the resume checklist's two groups, with what code checked apart from what is left to you", () => {
    const root = circuit();
    const card = root.querySelectorAll(".bj-circuit-card")[1];
    const body = text(card);
    expect(body).toContain("Checked by Between Jobs");
    expect(body).toContain("Yours to check");
    expect(body).toContain("Not checked");
    // A check nobody ran is never drawn as a pass: every "Not checked" row carries the plain circle.
    for (const row of card.querySelectorAll(".bj-circuit-checks li")) {
      const state = text(query(row, ".bj-circuit-check-state"));
      const mark = text(query(row, ".bj-circuit-mark"));
      expect(mark).toBe(state === "Pass" ? "✓" : "○");
    }
  });

  it("say the Gmail draft is optional and may be invite-only, and that the extension fills and leaves Submit to you", () => {
    const root = circuit();
    const cards = [...root.querySelectorAll(".bj-circuit-card")].map(text);
    expect(cards[2]).toContain("Gmail is optional, and may be invite-only for now");
    expect(cards[2]).toContain("You press Send.");
    expect(cards[4]).toContain("Left for you");
    expect(cards[4]).toContain("and Submit");
  });

  it("have summaries that say what each stage does, in plain sentences", () => {
    const root = circuit();
    const summaries = [...root.querySelectorAll(".bj-circuit-summary")].map(text);
    expect(summaries).toEqual(STAGES.map((stage) => stage.summary));
  });
});

describe("the board", () => {
  it("lists the statuses a tracked role can sit in, in order, and says the person moves it", () => {
    const root = circuit();
    const list = query(root, "ol.bj-circuit-statuses");
    expect([...list.querySelectorAll("li")].map((item) => text(item).replace(/^\S+\s/, ""))).toEqual(BOARD_STATUSES.map((status) => status.label));
    expect(text(query(root, ".bj-circuit-board"))).toContain("You move it between them.");
    // The only green is Offer.
    expect([...list.querySelectorAll(".bj-circuit-mark--pass")].map((mark) => text(mark.parentElement as Element))).toEqual(["✓ Offer"]);
  });
});

describe("the page's one cyan fill", () => {
  it("is not in the circuit: no primary button or primary link anywhere in it", () => {
    const root = circuit();
    expect(root.querySelectorAll(".bj-primary, .bj-button-link-primary")).toHaveLength(0);
    expect(root.querySelectorAll("a")).toHaveLength(0);
    expect(root.querySelector("input, form, img, svg")).toBeNull();
  });
});

describe("accessibility", () => {
  it("has no serious or critical axe violation", async () => {
    const { container } = render(
      <MemoryRouter>
        <CircuitDemo />
      </MemoryRouter>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});
