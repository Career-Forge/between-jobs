import { describe, expect, it } from "vitest";
import {
  applyFillPlan,
  attachFile,
  extractCustomQuestions,
  findCoverLetterField,
  isLeverApplyForm,
  planStandardFieldFills,
} from "@/lib/lever";
import type { ExtensionPersonalInfo } from "@/lib/types";

// Mirrors the real DOM structure confirmed live against
// jobs.lever.co/theathletic and jobs.lever.co/sysdig (2026-09-08) --
// standard fields by plain `name`, a genuine `cards[<uuid>]` custom
// question with the real `.application-field` -> parent
// `.application-label` nesting, a real `cards[<uuid>]` FILE field
// labeled "Cover Letter" (Sysdig's own real cover-letter field -- an
// opaque per-org custom field, not a fixed selector), plus a real
// `eeo[...]` field and a real `surveysResponses[<uuid>]` field (both
// must never be touched or reported as a "custom question").
function buildLeverForm(): void {
  document.body.innerHTML = `
    <form>
      <input type="file" name="resume" id="resume-upload-input" />
      <input type="text" name="name" />
      <input type="email" name="email" />
      <input type="text" name="phone" />
      <input type="text" name="location" id="location-input" />
      <input type="text" name="urls[LinkedIn]" />
      <input type="text" name="urls[Other (portfolio, GitHub etc)]" />

      <input type="hidden" name="cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][baseTemplate]" />
      <div>
        <div class="application-label">Did someone from The Athletic refer you? *</div>
        <div class="application-field full-width required-field">
          <ul>
            <li><label><input type="radio" name="cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field0]" value="Yes" />Yes</label></li>
            <li><label><input type="radio" name="cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field0]" value="No" />No</label></li>
          </ul>
          <input type="text" name="cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field1]" />
        </div>
      </div>

      <div>
        <div class="application-label">Cover Letter</div>
        <div class="application-field">
          <input type="file" name="cards[2b91c9dd-2899-4603-ab9f-b5218e738b4a][field0]" />
        </div>
      </div>

      <select name="eeo[gender]"><option value="Male">Male</option></select>
      <select name="eeo[race]"><option value="Asian">Asian</option></select>

      <input type="hidden" name="surveysResponses[74b096fa-42c0-4b49-b6a5-cb54e1e92537][surveyId]" />
      <input type="radio" name="surveysResponses[74b096fa-42c0-4b49-b6a5-cb54e1e92537][responses][field0]" value="18-20" />
    </form>
  `;
}

const FULL_INFO: ExtensionPersonalInfo = {
  name: "Jane Doe",
  email: "jane@example.com",
  phone: "+1-555-0100",
  location: { city: "New York", region: "NY", country: "US" },
  linkedin: "https://linkedin.com/in/jane",
  github: "",
  portfolio: "https://jane.dev",
};

describe("isLeverApplyForm", () => {
  it("detects a real Lever apply form by its resume input", () => {
    buildLeverForm();
    expect(isLeverApplyForm(document)).toBe(true);
  });

  it("returns false on an unrelated page", () => {
    document.body.innerHTML = "<div>Not an application form</div>";
    expect(isLeverApplyForm(document)).toBe(false);
  });
});

describe("planStandardFieldFills", () => {
  it("plans a fill for every empty standard field with available data", () => {
    buildLeverForm();
    const plan = planStandardFieldFills(document, FULL_INFO, false);
    const bySelector = Object.fromEntries(plan.map((p) => [p.selector, p.value]));

    expect(bySelector['input[name="name"]']).toBe("Jane Doe");
    expect(bySelector['input[name="email"]']).toBe("jane@example.com");
    expect(bySelector['input[name="phone"]']).toBe("+1-555-0100");
    expect(bySelector['input[name="location"]']).toBe("New York, NY, US");
    expect(bySelector['input[name="urls[LinkedIn]"]']).toBe("https://linkedin.com/in/jane");
    expect(bySelector['input[name="urls[Other (portfolio, GitHub etc)]"]']).toBe("https://jane.dev");
  });

  it("skips a field the user already filled in, by default (D5)", () => {
    buildLeverForm();
    const nameInput = document.querySelector<HTMLInputElement>('input[name="name"]')!;
    nameInput.value = "Already Typed";

    const plan = planStandardFieldFills(document, FULL_INFO, false);

    expect(plan.some((p) => p.selector === 'input[name="name"]')).toBe(false);
  });

  it("overwrites an already-filled field when forceRefillAll is true", () => {
    buildLeverForm();
    const nameInput = document.querySelector<HTMLInputElement>('input[name="name"]')!;
    nameInput.value = "Already Typed";

    const plan = planStandardFieldFills(document, FULL_INFO, true);

    expect(plan.find((p) => p.selector === 'input[name="name"]')?.value).toBe("Jane Doe");
  });

  it("skips a field with no available data instead of writing an empty string", () => {
    buildLeverForm();
    const info: ExtensionPersonalInfo = { ...FULL_INFO, email: null };

    const plan = planStandardFieldFills(document, info, false);

    expect(plan.some((p) => p.selector === 'input[name="email"]')).toBe(false);
  });

  it("skips a standard field that isn't present on this posting's form", () => {
    document.body.innerHTML = `<form><input type="file" name="resume" /><input type="text" name="name" /></form>`;
    const plan = planStandardFieldFills(document, FULL_INFO, false);
    expect(plan.every((p) => p.selector === 'input[name="name"]')).toBe(true);
  });
});

describe("applyFillPlan", () => {
  it("sets values and dispatches input/change events a page's own JS can observe", () => {
    buildLeverForm();
    const nameInput = document.querySelector<HTMLInputElement>('input[name="name"]')!;
    const events: string[] = [];
    nameInput.addEventListener("input", () => events.push("input"));
    nameInput.addEventListener("change", () => events.push("change"));

    const filled = applyFillPlan(document, [{ selector: 'input[name="name"]', value: "Jane Doe" }]);

    expect(nameInput.value).toBe("Jane Doe");
    expect(events).toEqual(["input", "change"]);
    expect(filled).toEqual(['input[name="name"]']);
  });

  it("silently skips a plan item whose selector no longer matches anything", () => {
    buildLeverForm();
    const filled = applyFillPlan(document, [{ selector: 'input[name="does-not-exist"]', value: "x" }]);
    expect(filled).toEqual([]);
  });
});

describe("extractCustomQuestions", () => {
  it("extracts a genuine custom question with its real question-level label", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document);
    const referral = questions.find((q) =>
      q.fieldName.includes("dbe2365b-6fb2-40f2-a0b8-9d370957da3b"),
    );
    expect(referral?.label).toBe("Did someone from The Athletic refer you? *");
  });

  it("never treats an eeo[...] field as a custom question (D6, by field-name construction)", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName.startsWith("eeo["))).toBe(false);
  });

  it("never treats a surveysResponses[...] field as a custom question (D6)", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName.startsWith("surveysResponses["))).toBe(false);
  });

  it("excludes the hidden baseTemplate field from a card", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName.endsWith("[baseTemplate]"))).toBe(false);
  });

  it("dedupes multiple radio inputs sharing the same field name into one question", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document);
    const matching = questions.filter((q) =>
      q.fieldName === "cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field0]",
    );
    expect(matching).toHaveLength(1);
  });

  it("tags a text question with kind 'text'", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document);
    const referral = questions.find((q) =>
      q.fieldName === "cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field1]",
    );
    expect(referral?.kind).toBe("text");
  });

  it("still returns a file-type card as kind 'file' when it isn't explicitly excluded -- regression guard for a real, adversarially-confirmed bug where EVERY file field was silently dropped, not just the cover-letter one", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document);
    const coverLetter = questions.find((q) =>
      q.fieldName.includes("2b91c9dd-2899-4603-ab9f-b5218e738b4a"),
    );
    expect(coverLetter).toBeDefined();
    expect(coverLetter?.kind).toBe("file");
    expect(coverLetter?.label).toBe("Cover Letter");
  });

  it("excludes exactly the caller-identified cover-letter field via excludeFieldName, without hiding other file fields", () => {
    document.body.innerHTML = `
      <form>
        <input type="file" name="resume" />
        <div>
          <div class="application-label">Cover Letter</div>
          <div class="application-field">
            <input type="file" name="cards[2b91c9dd-2899-4603-ab9f-b5218e738b4a][field0]" />
          </div>
        </div>
        <div>
          <div class="application-label">Portfolio Sample</div>
          <div class="application-field">
            <input type="file" name="cards[11111111-1111-1111-1111-111111111111][field0]" />
          </div>
        </div>
      </form>
    `;
    const coverLetterField = findCoverLetterField(document);
    const questions = extractCustomQuestions(document, coverLetterField);

    expect(questions.some((q) => q.fieldName.includes("2b91c9dd"))).toBe(false);
    const portfolio = questions.find((q) => q.fieldName.includes("11111111"));
    expect(portfolio?.kind).toBe("file");
    expect(portfolio?.label).toBe("Portfolio Sample");
  });
});

describe("findCoverLetterField", () => {
  it("finds a real cover-letter file field by its rendered label, not a fixed selector", () => {
    buildLeverForm();
    const fieldName = findCoverLetterField(document);
    expect(fieldName).toBe("cards[2b91c9dd-2899-4603-ab9f-b5218e738b4a][field0]");
  });

  it("returns null when no cover-letter field exists on this posting", () => {
    document.body.innerHTML = `<form><input type="file" name="resume" /></form>`;
    expect(findCoverLetterField(document)).toBeNull();
  });

  it("never matches the resume field itself or an unrelated file field", () => {
    document.body.innerHTML = `
      <form>
        <input type="file" name="resume" />
        <div>
          <div class="application-label">Portfolio Sample</div>
          <div class="application-field">
            <input type="file" name="cards[11111111-1111-1111-1111-111111111111][field0]" />
          </div>
        </div>
      </form>
    `;
    expect(findCoverLetterField(document)).toBeNull();
  });
});

describe("attachFile", () => {
  it("assigns a real File to the input's files and dispatches change", () => {
    buildLeverForm();
    const input = document.querySelector<HTMLInputElement>('input[name="resume"]')!;
    let changeFired = false;
    input.addEventListener("change", () => {
      changeFired = true;
    });

    // jsdom's native `HTMLInputElement.files` setter does its own strict
    // WebIDL type-check against an internal FileList brand that only
    // jsdom's own internals can produce -- confirmed directly: even a
    // shape-correct FileList-like object is rejected. That's a second,
    // separate jsdom gap on top of the missing DataTransfer constructor
    // (see tests/setup.ts), specific to this one property. Redefining it
    // as a plain writable property here lets this test verify what it's
    // actually responsible for -- that `attachFile` constructs the right
    // File and assigns *something* to `.files` before dispatching
    // `change` -- without that being blocked by an environment limitation
    // unrelated to this project's own code.
    Object.defineProperty(input, "files", { value: undefined, writable: true, configurable: true });

    const bytes = new TextEncoder().encode("%PDF-1.4 fake").buffer;
    attachFile(input, bytes, "resume.pdf", "application/pdf");

    expect(input.files).not.toBeUndefined();
    expect(input.files!.length).toBe(1);
    expect(input.files!.item(0)?.name).toBe("resume.pdf");
    expect(input.files!.item(0)?.type).toBe("application/pdf");
    expect(changeFired).toBe(true);
  });
});
