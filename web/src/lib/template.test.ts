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

// The importer sorts skills into six categories, and a model left to guess put BI,
// test-automation and observability tools under "programming" or "other". The prompt has to say
// where they go.
describe("CONVERSION_PROMPT skill sorting", () => {
  it("names the tools category for BI, test-automation and observability tools", () => {
    const lines = CONVERSION_PROMPT.replace(/\s+/g, " ");
    for (const tool of ["Tableau", "Power BI", "Looker", "Selenium", "Cypress", "JMeter", "Datadog"]) {
      expect(lines).toContain(tool);
    }
    expect(lines).toMatch(/Tableau, Power BI, Looker, Selenium, Cypress, JMeter and Datadog -- belong under "tools"/);
  });

  it("mentions every category key the template actually has", () => {
    for (const key of Object.keys(RESUME_TEMPLATE.skills)) {
      expect(CONVERSION_PROMPT).toContain(`"${key}"`);
    }
  });

  it("introduces the renamed label for data_mlops, not the old one", () => {
    expect(CONVERSION_PROMPT).toContain('"data_mlops" (shown to me as "Data & Pipelines")');
    expect(CONVERSION_PROMPT).not.toContain("MLOps\"");
  });
});
