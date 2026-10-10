import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ClaimWarningsBanner, claimOutcome, isRepairedClaim } from "./ClaimWarningsBanner";

// What the banner says it did must match what the warnings say was done. The separate engine
// only flags; the built-in engine has already removed the text or put the candidate's own
// wording back. (All text here is synthetic.)

const FLAGGED =
  'unsupported claim: "Led a team of 40 at Initech" -- the number "40" is not in your profile';
const REMOVED =
  'unsupported claim (removed before output): "I led a team of 40." -- "led" claims a role [cover letter]';
const REPLACED =
  'unsupported claim (replaced with your original wording): "Cut latency 45%" -- the number "45%" is not in the source bullet(s) [/experience/0]';

const NOT_EDITED = "Nothing was auto-edited or blocked";

function banner(warnings: string[]): string {
  return renderToStaticMarkup(<ClaimWarningsBanner warnings={warnings} />);
}

describe("ClaimWarningsBanner", () => {
  it("tells the truth about a warning the engine only flagged", () => {
    const html = banner([FLAGGED]);
    expect(html).toContain(NOT_EDITED);
    expect(html).toContain("Claim verification flagged 1 claim(s)");
    expect(html).toContain("the number &quot;40&quot; is not in your profile");
  });

  it.each([
    ["removed", [REMOVED]],
    ["replaced", [REPLACED]],
    ["both", [REMOVED, REPLACED]],
  ])("does not say nothing was edited when every claim was %s by the engine", (_name, warnings) => {
    const html = banner(warnings);
    expect(html).not.toContain(NOT_EDITED);
    expect(html).toContain("taken out of the document, or swapped for your own wording");
    expect(html).toContain(`flagged ${warnings.length} claim(s)`);
  });

  it("uses neutral wording when some claims were acted on and some were not", () => {
    const html = banner([REPLACED, FLAGGED]);
    expect(html).not.toContain(NOT_EDITED);
    expect(html).toContain("Where a line says it was removed or replaced");
    expect(html).toContain("the rest is still there");
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

  it("still lists every warning verbatim", () => {
    const html = banner([FLAGGED, REMOVED]);
    expect(html).toContain("Led a team of 40 at Initech");
    expect(html).toContain("I led a team of 40.");
  });
});
