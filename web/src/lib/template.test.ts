import { describe, expect, it } from "vitest";
import { CONVERSION_PROMPT, RESUME_TEMPLATE } from "./template";

// work-authorization-status.md P1: the template's work_authorization
// placeholder used to be a bare "" while every sibling field already had a
// <e.g. ...> placeholder -- exactly why a filled-in template with that
// field left untouched read as "the person has nothing to say," not "the
// person hasn't been asked yet."

describe("RESUME_TEMPLATE.personal.work_authorization", () => {
  it("is no longer a bare empty string", () => {
    expect(RESUME_TEMPLATE.personal.work_authorization).not.toBe("");
    expect(RESUME_TEMPLATE.personal.work_authorization.length).toBeGreaterThan(0);
  });

  it("reads as a fill-in-the-blank instruction, like its sibling fields, not a real answer", () => {
    const value = RESUME_TEMPLATE.personal.work_authorization;
    // Every sibling placeholder in this template is wrapped in angle
    // brackets precisely so a filling LLM parses it as "replace this,"
    // never as a literal value to carry through unedited.
    expect(value.startsWith("<")).toBe(true);
    expect(value.endsWith(">")).toBe(true);
  });

  it("is not US-centric -- no visa category tied to one country's system", () => {
    const value = RESUME_TEMPLATE.personal.work_authorization.toLowerCase();
    for (const usTerm of ["h-1b", "opt", "green card", "visa sponsorship"]) {
      expect(value).not.toContain(usTerm);
    }
  });

  it("is embedded in the conversion prompt a user pastes into an LLM", () => {
    expect(CONVERSION_PROMPT).toContain(RESUME_TEMPLATE.personal.work_authorization);
  });
});
