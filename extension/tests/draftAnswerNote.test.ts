import { describe, expect, it } from "vitest";
import { draftAnswerNote } from "@/lib/draftAnswerNote";

// What the side panel says beside Draft answer and Try again about what pressing them costs. The exact
// words are pinned here; tests/App.test.tsx pins that the sentence sits next to the button.

const EM_OR_EN_DASH = new RegExp(`[${String.fromCharCode(0x2013)}${String.fromCharCode(0x2014)}]`);

describe("draftAnswerNote", () => {
  it("says a press makes up to two real model calls on the person's own key, and none for a saved answer", () => {
    expect(draftAnswerNote("Draft answer")).toBe(
      "Pressing Draft answer makes up to two real model calls on your own key, one to draft the answer and one to check the claims in it, unless an answer you saved earlier fits this question.",
    );
  });

  it("names the button it is beside", () => {
    expect(draftAnswerNote("Try again")).toBe(
      "Pressing Try again makes up to two real model calls on your own key, one to draft the answer and one to check the claims in it, unless an answer you saved earlier fits this question.",
    );
  });

  it("says how many (never the singular), and uses no dash and none of the banned words", () => {
    for (const press of ["Draft answer", "Try again"] as const) {
      const note = draftAnswerNote(press);
      expect(note).toContain("up to two real model calls");
      expect(note).not.toContain("a real model call");
      expect(note).not.toMatch(EM_OR_EN_DASH);
      expect(note).not.toMatch(/verified|unknown model/i);
      expect(note.endsWith(".")).toBe(true);
    }
  });
});
