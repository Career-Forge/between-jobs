import { describe, expect, it } from "vitest";
import { flaggedCount, isClaimWarning, parseClaimWarning } from "./claimWarnings";

// Synthetic warnings in the two formats the engines write.

describe("parseClaimWarning", () => {
  it("reads the separate engine's flag: a verdict in the parenthesis, the claim, the reason", () => {
    expect(parseClaimWarning('unsupported claim (unverifiable): "Cut query costs by 40%" -- None of the picked achievements mention query costs.')).toEqual({
      status: "unverifiable",
      claim: "Cut query costs by 40%",
      reason: "None of the picked achievements mention query costs.",
      action: null,
      where: null,
    });
  });

  it("reads a contradicted claim as contradicted", () => {
    expect(parseClaimWarning('unsupported claim (contradicted): "Led 20 engineers" -- no match')?.status).toBe("contradicted");
  });

  it("reads the older format with no parenthesis as an unverifiable flag", () => {
    expect(parseClaimWarning('unsupported claim: "Led a team of 40 at Initech" -- the number "40" is not in your profile')).toMatchObject({
      status: "unverifiable",
      claim: "Led a team of 40 at Initech",
      reason: 'the number "40" is not in your profile',
      action: null,
    });
  });

  it.each([
    ["removed before output", "Taken out before the document was written."],
    ["replaced with your original wording", "Replaced with your original wording before the document was written."],
    ["removed", "Taken out of the document."],
  ])("says what the built-in engine did when it %s, and draws the claim as unverifiable", (disposition, words) => {
    const parsed = parseClaimWarning(`unsupported claim (${disposition}): "Cut latency 45%" -- the number "45%" is not in the source bullet(s) [/experience/0]`);
    expect(parsed).toMatchObject({ status: "unverifiable", action: words, claim: "Cut latency 45%" });
    expect(parsed?.reason).toBe('the number "45%" is not in the source bullet(s)');
  });

  it("names the part of the document when the bracket does in words, and drops a bracket that is only a pointer", () => {
    expect(parseClaimWarning('unsupported claim (removed before output): "I led a team of 40." -- "led" claims a role [cover letter]')?.where).toBe("In the cover letter");
    expect(parseClaimWarning('unsupported claim (removed before output): "Ten years of Rust." -- spelled years [summary]')?.where).toBe("In the summary");
    expect(parseClaimWarning('unsupported claim (replaced with your original wording): "x y" -- reason [/experience/3]')?.where).toBeNull();
  });

  it("keeps a quotation mark inside the claim", () => {
    expect(parseClaimWarning('unsupported claim (unverifiable): "Built the "core" service" -- not found')?.claim).toBe('Built the "core" service');
  });

  it("keeps a reason that has its own dashes", () => {
    expect(parseClaimWarning('unsupported claim (unverifiable): "Owned billing" -- no billing work -- nothing in the picked achievements')?.reason).toBe(
      "no billing work -- nothing in the picked achievements",
    );
  });

  it.each([
    ["a pin warning", 'pinned item "Acme" got 1 of 3 bullets'],
    ["no quoted claim", "unsupported claim (contradicted): Led 20 engineers -- no match"],
    ["an empty claim", 'unsupported claim (contradicted): "" -- no match'],
    ["an empty string", ""],
  ])("does not read %s as a claim", (_name, warning) => {
    expect(parseClaimWarning(warning)).toBeNull();
  });
});

describe("claim warnings among the others", () => {
  it("counts only the claim warnings", () => {
    const warnings = ['unsupported claim (unverifiable): "a" -- b', 'pinned item "x"', "unsupported claim: not quoted"];
    expect(warnings.filter(isClaimWarning)).toHaveLength(2);
    expect(flaggedCount(warnings)).toBe(2);
    expect(flaggedCount([])).toBe(0);
  });
});
