// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { buttonNamed, click, query, render } from "../testing/render";
import { settle, text } from "../testing/forms";
import { CLAIM_CLOSING, CLAIM_LEGEND, CLAIM_MODEL_NOTE, ClaimWarningsBanner, ZERO_FLAG_CAVEAT, claimOutcome, isRepairedClaim } from "./ClaimWarningsBanner";

// What the block says it did must match what the warnings say was done. The separate engine only flags;
// the built-in engine has already removed the text or put the candidate's own wording back. (All text
// here is synthetic.)

const FLAGGED =
  'unsupported claim: "Led a team of 40 at Initech" -- the number "40" is not in your profile';
const CONTRADICTED = 'unsupported claim (contradicted): "Led 20 engineers" -- no match';
const REMOVED =
  'unsupported claim (removed before output): "I led a team of 40." -- "led" claims a role [cover letter]';
const REPLACED =
  'unsupported claim (replaced with your original wording): "Cut latency 45%" -- the number "45%" is not in the source bullet(s) [/experience/0]';

const NOT_EDITED = "Nothing was auto-edited or blocked";

function block(warnings: string[]) {
  return render(
    <MemoryRouter>
      <ClaimWarningsBanner warnings={warnings} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ClaimWarningsBanner", () => {
  it("tells the truth about a warning the engine only flagged", () => {
    const { container } = block([FLAGGED]);
    const words = text(container);
    expect(words).toContain(NOT_EDITED);
    expect(words).toContain("1 claim flagged");
    expect(words).toContain('the number "40" is not in your profile');
  });

  it.each([
    ["removed", [REMOVED]],
    ["replaced", [REPLACED]],
    ["both", [REMOVED, REPLACED]],
  ])("does not say nothing was edited when every claim was %s by the engine", (_name, warnings) => {
    const words = text(block(warnings).container);
    expect(words).not.toContain(NOT_EDITED);
    expect(words).toContain("taken out of the document, or swapped for your own wording");
    expect(words).toContain(`${warnings.length} ${warnings.length === 1 ? "claim" : "claims"} flagged`);
  });

  it("uses neutral wording when some claims were acted on and some were not", () => {
    const words = text(block([REPLACED, FLAGGED]).container);
    expect(words).not.toContain(NOT_EDITED);
    expect(words).toContain("Where a line says it was removed or replaced");
    expect(words).toContain("the rest is still there");
  });

  it("reads the disposition from the warning, not from anywhere else", () => {
    expect(isRepairedClaim(REMOVED)).toBe(true);
    expect(isRepairedClaim(REPLACED)).toBe(true);
    expect(isRepairedClaim(FLAGGED)).toBe(false);
    expect(isRepairedClaim("pinned item was cut")).toBe(false);
    expect(claimOutcome([])).toBe("none-repaired");
    expect(claimOutcome([FLAGGED, FLAGGED])).toBe("none-repaired");
    expect(claimOutcome([REMOVED, REMOVED])).toBe("all-repaired");
    expect(claimOutcome([REMOVED, FLAGGED])).toBe("mixed");
  });

  it("still lists every claim verbatim, in curly quotes, in the claim's own words", () => {
    const { container } = block([FLAGGED, REMOVED]);
    const claims = [...container.querySelectorAll(".bj-claimrow-claim")].map((node) => node.textContent);
    expect(claims).toEqual(["“Led a team of 40 at Initech”", "“I led a team of 40.”"]);
  });
});

describe("the rows", () => {
  it("draws a flagged claim as unverifiable and a contradicted one as contradicted, each with a word and the same gold mark, and neither red", () => {
    const { container } = block([FLAGGED, CONTRADICTED]);
    const chips = [...container.querySelectorAll(".bj-claimrow-chip")].map((chip) => text(chip));
    expect(chips).toEqual(["◆ Unverifiable", "◆ Contradicted -- a model's reading, may be wrong"]);
    expect(container.querySelector(".bj-claimrow--unverifiable")).not.toBeNull();
    expect(container.querySelector(".bj-claimrow--contradicted")).not.toBeNull();
    // A model's reading is not a failed check: no cross and no danger tone anywhere in the block.
    expect(container.textContent).not.toContain("✕");
    expect(container.innerHTML).not.toMatch(/danger|trail-glyph--red/);
    expect([...container.querySelectorAll(".bj-claimrow-chip .bj-trail-glyph")].map((glyph) => glyph.className)).toEqual([
      "bj-trail-glyph bj-trail-glyph--gold",
      "bj-trail-glyph bj-trail-glyph--gold",
    ]);
  });

  it("says over the rows that a model flagged them and may be wrong, once, and only when there are rows", () => {
    expect(CLAIM_MODEL_NOTE).toBe("A model flagged these claims and may be wrong about any of them, including the ones marked Contradicted.");
    const { container } = block([FLAGGED, CONTRADICTED]);
    expect(text(container).split(CLAIM_MODEL_NOTE)).toHaveLength(2);
    const note = [...container.querySelectorAll("p")].find((line) => text(line) === CLAIM_MODEL_NOTE)!;
    expect(note.compareDocumentPosition(query(container, "ul[aria-label='Flagged claims']")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Nothing flagged: no rows, so no line about flagged claims.
    expect(text(block([]).container)).not.toContain(CLAIM_MODEL_NOTE);
    // A warning that cannot be read as a claim is plain text, not a row: the line is about rows.
    expect(text(block(["unsupported claim (contradicted): Led 20 engineers -- no match"]).container)).not.toContain(CLAIM_MODEL_NOTE);
  });

  it("keeps the flags and the footer as they were: a contradicted claim still counts as flagged, and nothing is blocked", () => {
    const { container } = block([FLAGGED, CONTRADICTED]);
    expect(text(container)).toContain("2 claims flagged");
    expect(text(container)).toContain(CLAIM_CLOSING["none-repaired"]);
    expect(text(container)).toContain("Flags only. Nothing was auto-edited or blocked");
  });

  it("says what the built-in engine did to a claim, and which part of the document it was in", () => {
    const words = text(block([REMOVED]).container);
    expect(words).toContain("Taken out before the document was written.");
    expect(words).toContain("In the cover letter");
  });

  it("offers the two things a person can do with a claim: add it to the profile if it is true, or copy it", () => {
    const { container } = block([FLAGGED]);
    const link = [...container.querySelectorAll("a")].find((a) => text(a) === "If it's true, add it to your profile");
    expect(link?.getAttribute("href")).toBe("/profile");
    expect(buttonNamed(container, "Copy claim")).toBeTruthy();
  });

  it("copies the claim and says so, and says so when the browser refuses", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const { container } = block([FLAGGED]);
    click(buttonNamed(container, "Copy claim"));
    await settle();
    expect(writeText).toHaveBeenCalledWith("Led a team of 40 at Initech");
    expect(text(container)).toContain("Copied.");

    writeText.mockRejectedValue(new Error("denied"));
    click(buttonNamed(container, "Copy claim"));
    await settle();
    expect(text(container)).toContain("Couldn't copy");
  });

  it("shows a warning it cannot read as a claim as plain text, never as a quotation", () => {
    const { container } = block(["unsupported claim (contradicted): Led 20 engineers -- no match"]);
    expect(container.querySelector(".bj-claimrow")).toBeNull();
    expect(text(container.querySelector('[aria-label="Other claim notes"]')!)).toContain("Led 20 engineers -- no match");
  });
});

// The band's "Read the flagged claims" is a link to this heading (workspace.ts), and the page's hash scroll
// focuses a heading that has a tabindex. Both views draw it, and once: an id that appeared twice would
// make the link land on whichever came first.
describe("the heading the band links to", () => {
  it.each([
    ["with flagged claims", [FLAGGED, CONTRADICTED]],
    ["with nothing flagged", []],
  ])("is the one element with the id, a focusable h3 reading Claim check, %s", (_name, warnings) => {
    const { container } = block(warnings);
    const heading = query(container, "[id='claim-check-heading']");
    expect(container.querySelectorAll("[id='claim-check-heading']")).toHaveLength(1);
    expect(heading.tagName).toBe("H3");
    expect(heading.getAttribute("tabindex")).toBe("-1");
    expect(text(heading)).toBe("Claim check");
  });
});

describe("what is always said", () => {
  it("keeps the legend visible: only flagged claims are listed, and one that was not flagged is not supported", () => {
    expect(text(block([FLAGGED]).container)).toContain(CLAIM_LEGEND);
    expect(text(block([]).container)).toContain(CLAIM_LEGEND);
    expect(CLAIM_LEGEND).toContain("claims that weren't flagged aren't listed");
  });

  it("words the legend's Contradicted as a model's reading, with the same gold mark as Unverifiable and no cross", () => {
    expect(CLAIM_LEGEND).toBe("◆ Unverifiable · ◆ Contradicted -- a model's reading, may be wrong · claims that weren't flagged aren't listed");
    expect(CLAIM_LEGEND).not.toContain("✕");
  });

  it("says 'No claims flagged in this run' with the caveat, and never a count of grounded claims or a green mark", () => {
    const { container } = block([]);
    const words = text(container);
    expect(words).toContain("No claims flagged in this run");
    expect(words).toContain(ZERO_FLAG_CAVEAT);
    expect(ZERO_FLAG_CAVEAT).toContain("Not flagged isn't verified");
    expect(container.querySelector(".bj-claimrow")).toBeNull();
    expect(words).not.toMatch(/grounded|supported|verified\b(?!:)/i);
    expect(container.innerHTML).not.toContain("emerald");
  });

  it("passes an accessibility check with rows, and with none", async () => {
    await expectNoSeriousA11yViolations(block([FLAGGED, CONTRADICTED, REMOVED]).container);
    await expectNoSeriousA11yViolations(block([]).container);
  });
});
