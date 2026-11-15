// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { describedText } from "./forms";

// The helper the spend-note tests read a button's description with. Two of its refusals are the only guard in many
// tests (an id that names nothing, a note that is hidden), so they are pinned here.

afterEach(() => {
  document.body.innerHTML = "";
});

function page(html: string): HTMLElement {
  document.body.innerHTML = html;
  return document.getElementById("go")!;
}

describe("describedText", () => {
  it("is empty for a control with no aria-describedby, or an empty one", () => {
    expect(describedText(page('<button id="go">Go</button>'))).toBe("");
    expect(describedText(page('<button id="go" aria-describedby="  ">Go</button>'))).toBe("");
  });

  it("reads the note's words, whitespace collapsed", () => {
    expect(describedText(page('<button id="go" aria-describedby="n">Go</button><p id="n">Pressing  Go\n costs a call.</p>'))).toBe("Pressing Go costs a call.");
  });

  it("joins several notes in the order the ids are listed, not the order they sit on the page", () => {
    expect(describedText(page('<button id="go" aria-describedby="b a">Go</button><p id="a">First.</p><p id="b">Second.</p>'))).toBe("Second. First.");
  });

  it("throws for an id that names nothing, naming the id", () => {
    expect(() => describedText(page('<button id="go" aria-describedby="missing">Go</button>'))).toThrow("aria-describedby names #missing, which is not on the page");
    expect(() => describedText(page('<button id="go" aria-describedby="n gone"><p id="n">x</p></button>'))).toThrow("#gone");
  });

  it.each([
    ["a hidden note", '<p id="n" hidden>Costs a call.</p>'],
    ["an aria-hidden note", '<p id="n" aria-hidden="true">Costs a call.</p>'],
    ["a visually hidden note", '<p id="n" class="bj-visually-hidden">Costs a call.</p>'],
    ["a note inside a hidden element", '<div hidden><p id="n">Costs a call.</p></div>'],
    ["a note inside an aria-hidden element", '<div aria-hidden="true"><p id="n">Costs a call.</p></div>'],
    ["a note inside a visually hidden element", '<div class="bj-visually-hidden"><p id="n">Costs a call.</p></div>'],
  ])("throws for %s, since the cost has to be words everyone can read", (_name, note) => {
    expect(() => describedText(page(`<button id="go" aria-describedby="n">Go</button>${note}`))).toThrow("which is hidden");
  });

  it("does not throw for a note that is merely small, muted or aria-hidden=false", () => {
    expect(describedText(page('<button id="go" aria-describedby="n">Go</button><p id="n" class="bj-muted bj-small" aria-hidden="false">Costs a call.</p>'))).toBe("Costs a call.");
  });
});
