import { describe, expect, it } from "vitest";
import { A_MODEL_CALL, SEVERAL_MODEL_CALLS, spendNotice } from "./spendNotice";

// The shape every "what pressing this costs" sentence is built in. The exact words are pinned here once;
// the controls that use them pin only that their sentence sits next to them.

const EM_OR_EN_DASH = /[\u2013\u2014]/;

describe("spendNotice", () => {
  it("keeps the two spend phrases word for word", () => {
    expect(A_MODEL_CALL).toBe("a real model call on your own key");
    expect(SEVERAL_MODEL_CALLS).toBe("several real model calls on your own key");
  });

  it("builds the one sentence for a single call", () => {
    expect(spendNotice("Check coverage", A_MODEL_CALL)).toBe("Pressing Check coverage makes a real model call on your own key.");
  });

  it("builds the same sentence for a press that makes several calls", () => {
    expect(spendNotice("Generate resume", SEVERAL_MODEL_CALLS)).toBe("Pressing Generate resume makes several real model calls on your own key.");
  });

  it("carries words that go on after the spend, and still ends in one period", () => {
    expect(spendNotice("Search again", `${A_MODEL_CALL} and uses any search keys you have connected`)).toBe(
      "Pressing Search again makes a real model call on your own key and uses any search keys you have connected.",
    );
  });

  it("opens with Pressing unless it is told the work starts on a choice", () => {
    expect(spendNotice("Check coverage", A_MODEL_CALL, "Pressing")).toBe(spendNotice("Check coverage", A_MODEL_CALL));
    expect(spendNotice("a file", `${A_MODEL_CALL}, and a second one if the first answer can't be read`, "Choosing")).toBe(
      "Choosing a file makes a real model call on your own key, and a second one if the first answer can't be read.",
    );
  });

  it("starts with the press and ends with a single period, whatever it is given", () => {
    for (const [press, what] of [
      ["Check coverage", A_MODEL_CALL],
      ["Generate resume", SEVERAL_MODEL_CALLS],
      ["Close the gaps", `${A_MODEL_CALL} to read the posting, and a second one if it finds a gap worth asking about`],
    ] as const) {
      const sentence = spendNotice(press, what);
      expect(sentence.startsWith(`Pressing ${press} makes `)).toBe(true);
      expect(sentence.endsWith(".")).toBe(true);
      expect(sentence.endsWith("..")).toBe(false);
    }
  });

  it("uses no em dash or en dash and none of the words the trail copy bans", () => {
    for (const phrase of [A_MODEL_CALL, SEVERAL_MODEL_CALLS, spendNotice("Check coverage", A_MODEL_CALL), spendNotice("Generate resume", SEVERAL_MODEL_CALLS)]) {
      expect(phrase).not.toMatch(EM_OR_EN_DASH);
      expect(phrase).not.toMatch(/verified|unknown model/i);
    }
  });

  it("never calls a model call a search call, and says the key is the person's own", () => {
    expect(A_MODEL_CALL).not.toMatch(/search/i);
    expect(SEVERAL_MODEL_CALLS).not.toMatch(/search/i);
    expect(A_MODEL_CALL).toContain("on your own key");
    expect(SEVERAL_MODEL_CALLS).toContain("on your own key");
  });
});
