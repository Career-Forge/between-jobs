import { describe, expect, it } from "vitest";
import type { LeverFieldMap } from "@/lib/ats-field-map";
import {
  applyFillPlan,
  attachFile,
  extractCustomQuestions,
  fillCustomTextAnswer,
  findCoverLetterField,
  GENERIC_FIELD_DEFAULTS,
  isLeverApplyForm,
  planStandardFieldFills,
} from "@/lib/lever";
import type { ExtensionPersonalInfo } from "@/lib/types";

// E3c -- the genuinely Lever-idiosyncratic half of the field map
// (location/LinkedIn/portfolio, the cards[ prefix, the label-lookup
// selectors, the cover-letter pattern), exactly what a real signed
// payload would carry post-verification. Merged with
// GENERIC_FIELD_DEFAULTS.standardFields (open-source, unsigned) for the
// `planStandardFieldFills` tests below, mirroring how content.ts itself
// merges them.
const TEST_LEVER_MAP: LeverFieldMap = {
  ats_type: "lever",
  version: 1,
  schema: "ats-field-map/v1",
  standard_fields: [
    {
      field: "location",
      selector: 'input[name="location"]',
      strategy: "joinNonEmpty",
      profileFields: ["location.city", "location.region", "location.country"],
      separator: ", ",
    },
    {
      field: "linkedin",
      selector: 'input[name="urls[LinkedIn]"]',
      strategy: "direct",
      profileFields: ["linkedin"],
    },
    {
      field: "portfolio",
      selector: 'input[name="urls[Other (portfolio, GitHub etc)]"]',
      strategy: "fallback",
      profileFields: ["portfolio", "github"],
    },
  ],
  custom_question_prefix: "cards[",
  label_wrapper_selector: ".application-field",
  label_selector: ".application-label",
  cover_letter_label_pattern: "cover letter",
};

const ALL_STANDARD_FIELDS = [...GENERIC_FIELD_DEFAULTS.standardFields, ...TEST_LEVER_MAP.standard_fields];

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
    const plan = planStandardFieldFills(document, FULL_INFO, false, ALL_STANDARD_FIELDS);
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

    const plan = planStandardFieldFills(document, FULL_INFO, false, ALL_STANDARD_FIELDS);

    expect(plan.some((p) => p.selector === 'input[name="name"]')).toBe(false);
  });

  it("overwrites an already-filled field when forceRefillAll is true", () => {
    buildLeverForm();
    const nameInput = document.querySelector<HTMLInputElement>('input[name="name"]')!;
    nameInput.value = "Already Typed";

    const plan = planStandardFieldFills(document, FULL_INFO, true, ALL_STANDARD_FIELDS);

    expect(plan.find((p) => p.selector === 'input[name="name"]')?.value).toBe("Jane Doe");
  });

  it("skips a field with no available data instead of writing an empty string", () => {
    buildLeverForm();
    const info: ExtensionPersonalInfo = { ...FULL_INFO, email: null };

    const plan = planStandardFieldFills(document, info, false, ALL_STANDARD_FIELDS);

    expect(plan.some((p) => p.selector === 'input[name="email"]')).toBe(false);
  });

  it("skips a standard field that isn't present on this posting's form", () => {
    document.body.innerHTML = `<form><input type="file" name="resume" /><input type="text" name="name" /></form>`;
    const plan = planStandardFieldFills(document, FULL_INFO, false, ALL_STANDARD_FIELDS);
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
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
    const referral = questions.find((q) =>
      q.fieldName.includes("dbe2365b-6fb2-40f2-a0b8-9d370957da3b"),
    );
    expect(referral?.label).toBe("Did someone from The Athletic refer you? *");
  });

  it("never treats an eeo[...] field as a custom question (D6, by field-name construction)", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
    expect(questions.some((q) => q.fieldName.startsWith("eeo["))).toBe(false);
  });

  it("never treats a surveysResponses[...] field as a custom question (D6)", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
    expect(questions.some((q) => q.fieldName.startsWith("surveysResponses["))).toBe(false);
  });

  it("excludes the hidden baseTemplate field from a card", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
    expect(questions.some((q) => q.fieldName.endsWith("[baseTemplate]"))).toBe(false);
  });

  it("dedupes multiple radio inputs sharing the same field name into one question", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
    const matching = questions.filter((q) =>
      q.fieldName === "cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field0]",
    );
    expect(matching).toHaveLength(1);
  });

  it("tags a text question with kind 'text'", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
    const referral = questions.find((q) =>
      q.fieldName === "cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field1]",
    );
    expect(referral?.kind).toBe("text");
  });

  it("tags a radio-group question with kind 'radio', not 'text' -- E3b's LLM-answer feature must never target a binary choice, since these are disproportionately the sensitive ones (work authorization, sponsorship)", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
    const referralYesNo = questions.find((q) =>
      q.fieldName === "cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field0]",
    );
    expect(referralYesNo?.kind).toBe("radio");
  });

  it("still returns a file-type card as kind 'file' when it isn't explicitly excluded -- regression guard for a real, adversarially-confirmed bug where EVERY file field was silently dropped, not just the cover-letter one", () => {
    buildLeverForm();
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP);
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
    const coverLetterField = findCoverLetterField(document, TEST_LEVER_MAP);
    const questions = extractCustomQuestions(document, TEST_LEVER_MAP, coverLetterField);

    expect(questions.some((q) => q.fieldName.includes("2b91c9dd"))).toBe(false);
    const portfolio = questions.find((q) => q.fieldName.includes("11111111"));
    expect(portfolio?.kind).toBe("file");
    expect(portfolio?.label).toBe("Portfolio Sample");
  });
});

describe("findCoverLetterField", () => {
  it("finds a real cover-letter file field by its rendered label, not a fixed selector", () => {
    buildLeverForm();
    const fieldName = findCoverLetterField(document, TEST_LEVER_MAP);
    expect(fieldName).toBe("cards[2b91c9dd-2899-4603-ab9f-b5218e738b4a][field0]");
  });

  it("returns null when no cover-letter field exists on this posting", () => {
    document.body.innerHTML = `<form><input type="file" name="resume" /></form>`;
    expect(findCoverLetterField(document, TEST_LEVER_MAP)).toBeNull();
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
    expect(findCoverLetterField(document, TEST_LEVER_MAP)).toBeNull();
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

describe("fillCustomTextAnswer", () => {
  it("fills a genuine cards[...] text field and dispatches events", () => {
    buildLeverForm();
    const fieldName = "cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field1]";
    const input = document.querySelector<HTMLInputElement>(`[name="${fieldName}"]`)!;
    const events: string[] = [];
    input.addEventListener("input", () => events.push("input"));
    input.addEventListener("change", () => events.push("change"));

    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, fieldName, "Jane Doe referred me.");

    expect(filled).toBe("filled");
    expect(input.value).toBe("Jane Doe referred me.");
    expect(events).toEqual(["input", "change"]);
  });

  it("refuses a field name outside the cards[...] namespace, e.g. an eeo[...] field, without even querying the DOM for it", () => {
    buildLeverForm();
    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, "eeo[gender]", "Male");
    expect(filled).toBe("refused");
  });

  it("refuses a radio-type cards[...] field -- E3b never generates a binary-choice answer", () => {
    buildLeverForm();
    const fieldName = "cards[dbe2365b-6fb2-40f2-a0b8-9d370957da3b][field0]";
    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, fieldName, "Yes");
    expect(filled).toBe("refused");
  });

  it("refuses a file-type cards[...] field (the cover-letter slot)", () => {
    buildLeverForm();
    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, "cards[2b91c9dd-2899-4603-ab9f-b5218e738b4a][field0]",
      "not a real file",
    );
    expect(filled).toBe("refused");
  });

  it("refuses the standard resume field even if somehow asked to fill it", () => {
    buildLeverForm();
    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, "resume", "not applicable");
    expect(filled).toBe("refused");
  });

  it("returns false for a field name that doesn't exist on the page", () => {
    buildLeverForm();
    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, "cards[00000000-0000-0000-0000-000000000000][field0]", "x");
    expect(filled).toBe("refused");
  });

  it("refuses a crafted field name that tries to break out of the attribute selector into an unrelated field", () => {
    // A `"` inside fieldName, left unescaped, would turn `[name="${fieldName}"]`
    // into TWO selectors joined by a real comma -- querySelector then matches
    // whichever comes first in DOM order, here the unrelated urls[LinkedIn]
    // field, even though the nonexistent cards[x] target never matched.
    buildLeverForm();
    const linkedin = document.querySelector<HTMLInputElement>('[name="urls[LinkedIn]"]')!;
    linkedin.value = "https://linkedin.com/in/janedoe";

    const maliciousFieldName = 'cards[x]"], input[name="urls[LinkedIn]';
    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, maliciousFieldName, "attacker-controlled text");

    expect(filled).toBe("refused");
    expect(linkedin.value).toBe("https://linkedin.com/in/janedoe");
  });
});

// ---------------------------------------------------------------------------
// E6 hardening. The namespace (`cards[`) is chosen by the tenant, and so is
// every label -- anyone can create a Lever account -- so these run against
// tenant-authored questions, not just Lever's own EEO fields.
// ---------------------------------------------------------------------------

// Built from char codes so this file never contains a raw invisible character.
const ZWSP = String.fromCharCode(0x200b);
const RLO = String.fromCharCode(0x202e);
const LRI = String.fromCharCode(0x2066);
const BEL = String.fromCharCode(0x07);

function cardQuestion(label: string | null, control: string): string {
  const labelHtml = label === null ? "" : `<div class="application-label">${label}</div>`;
  return `
    <div>
      ${labelHtml}
      <div class="application-field">${control}</div>
    </div>`;
}

function buildCards(...cards: string[]): void {
  document.body.innerHTML = `<form><input type="file" name="resume" />${cards.join("\n")}</form>`;
}

const textCard = (uuid: string) => `<input type="text" name="cards[${uuid}][field0]" />`;
const nameOf = (uuid: string) => `cards[${uuid}][field0]`;

describe("E6: control classification fails closed (Lever)", () => {
  it("lists a native <select> (how Lever renders a Dropdown question) as human-only, never as free text", () => {
    buildCards(
      cardQuestion(
        "Work authorization?",
        `<select name="cards[u2][field0]"><option>Yes</option><option>No</option></select>`,
      ),
    );
    const q = extractCustomQuestions(document, TEST_LEVER_MAP).find((x) => x.fieldName === nameOf("u2"));
    expect(q?.label).toBe("Work authorization?");
    expect(q?.kind).toBe("radio");
  });

  it.each(["date", "number", "password", "search"])("lists an <input type=%s> as human-only", (type) => {
    buildCards(cardQuestion("Earliest start date", `<input type="${type}" name="cards[u3][field0]" />`));
    const q = extractCustomQuestions(document, TEST_LEVER_MAP).find((x) => x.fieldName === nameOf("u3"));
    expect(q?.kind).toBe("radio");
  });

  it("still lists a plain text input and a textarea as text", () => {
    buildCards(
      cardQuestion("Why us?", `<input type="text" name="cards[t1][field0]" />`),
      cardQuestion("Tell us more", `<textarea name="cards[t2][field0]"></textarea>`),
    );
    const kinds = Object.fromEntries(extractCustomQuestions(document, TEST_LEVER_MAP).map((q) => [q.fieldName, q.kind]));
    expect(kinds[nameOf("t1")]).toBe("text");
    expect(kinds[nameOf("t2")]).toBe("text");
  });

  it("never writes to a select even when asked directly", () => {
    buildCards(cardQuestion("Work authorization?", `<select name="cards[u2][field0]"><option>Yes</option></select>`));
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("u2"), "Yes")).toBe("refused");
  });
});

describe("E6: D6 label check on the tenant-authored cards[ namespace (Lever)", () => {
  const SENSITIVE = [
    "What is your gender identity?",
    "What are your preferred pronouns?",
    "Do you require accommodations?",
    "Do you have a disability? Please describe.",
    "Are you a veteran?",
    "What is your ethnic background?",
    "What is your date of birth?",
  ];

  for (const label of SENSITIVE) {
    it(`excludes ${JSON.stringify(label)} from the question list and refuses to fill it`, () => {
      buildCards(cardQuestion(label, textCard("s1")));
      expect(extractCustomQuestions(document, TEST_LEVER_MAP).some((q) => q.fieldName === nameOf("s1"))).toBe(false);

      const input = document.querySelector<HTMLInputElement>(`[name="${nameOf("s1")}"]`)!;
      expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("s1"), "any text")).toBe("refused");
      expect(input.value).toBe("");
    });
  }

  it("excludes a sensitive card even when its label was padded past the display cap", () => {
    const padded = `${"Please tell us about yourself. ".repeat(20)}What is your gender?`;
    buildCards(cardQuestion(padded, textCard("s2")));
    expect(extractCustomQuestions(document, TEST_LEVER_MAP).some((q) => q.fieldName === nameOf("s2"))).toBe(false);
  });

  it("does NOT exclude a free-text work-authorization question -- a maintainer decision, pinned here", () => {
    buildCards(cardQuestion("Do you now or in the future require visa sponsorship?", textCard("w1")));
    const q = extractCustomQuestions(document, TEST_LEVER_MAP).find((x) => x.fieldName === nameOf("w1"));
    expect(q?.kind).toBe("text");
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("w1"), "No")).toBe("filled");
  });
});

describe("E6: an unreadable label is human-only (Lever)", () => {
  it("downgrades a text field with no label to kind 'radio' -- its only 'question text' would be a raw field name", () => {
    buildCards(cardQuestion(null, textCard("n1")));
    const q = extractCustomQuestions(document, TEST_LEVER_MAP).find((x) => x.fieldName === nameOf("n1"));
    expect(q).toBeDefined();
    expect(q?.label).toBeNull();
    expect(q?.kind).toBe("radio");
  });

  it("refuses to fill a field whose label can't be read", () => {
    buildCards(cardQuestion(null, textCard("n1")));
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("n1"), "text")).toBe("refused");
  });

  it("refuses (rather than throwing) when the signed map's label selectors don't parse", () => {
    buildCards(cardQuestion("Why us?", textCard("t1")));
    const brokenMap = { ...TEST_LEVER_MAP, label_wrapper_selector: "div[" };
    expect(() => fillCustomTextAnswer(document, brokenMap, nameOf("t1"), "x")).not.toThrow();
    expect(fillCustomTextAnswer(document, brokenMap, nameOf("t1"), "x")).toBe("refused");
  });
});

describe("E6: D5 -- a per-question fill never overwrites text already on the page (Lever)", () => {
  it("returns 'not_empty' and leaves a hand-typed answer untouched", () => {
    buildCards(cardQuestion("Why us?", textCard("t1")));
    const input = document.querySelector<HTMLInputElement>(`[name="${nameOf("t1")}"]`)!;
    input.value = "my own hand-written answer";
    const events: string[] = [];
    input.addEventListener("input", () => events.push("input"));

    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("t1"), "LLM draft")).toBe("not_empty");
    expect(input.value).toBe("my own hand-written answer");
    expect(events).toEqual([]);
  });

  it("treats whitespace-only content as empty", () => {
    buildCards(cardQuestion("Why us?", textCard("t1")));
    document.querySelector<HTMLInputElement>(`[name="${nameOf("t1")}"]`)!.value = "   ";
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("t1"), "LLM draft")).toBe("filled");
  });

  it("also protects a textarea", () => {
    buildCards(cardQuestion("Tell us more", `<textarea name="cards[t2][field0]"></textarea>`));
    const area = document.querySelector<HTMLTextAreaElement>(`[name="${nameOf("t2")}"]`)!;
    area.value = "already here";
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("t2"), "LLM draft")).toBe("not_empty");
    expect(area.value).toBe("already here");
  });
});

// ---------------------------------------------------------------------------
// E6 continuation -- the per-question "Replace" action's `force` parameter,
// the only escape hatch for D5's "not_empty" on a custom question ("Refill
// all" never touches these). Deliberately proves the narrow scope of what
// `force` bypasses: the D5 not-empty check ONLY, never the D6 sensitive-
// label refusal or the namespace/element-kind refusals above it.
// ---------------------------------------------------------------------------

describe("E6 continuation: fillCustomTextAnswer's force parameter (Lever)", () => {
  it("force:true overwrites existing text", () => {
    buildCards(cardQuestion("Why us?", textCard("t1")));
    document.querySelector<HTMLInputElement>(`[name="${nameOf("t1")}"]`)!.value = "my own hand-written answer";

    const filled = fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("t1"), "Replacement draft", true);

    expect(filled).toBe("filled");
    expect(document.querySelector<HTMLInputElement>(`[name="${nameOf("t1")}"]`)!.value).toBe("Replacement draft");
  });

  it("force:false (the default) still never overwrites -- the existing D5 behavior is unchanged", () => {
    buildCards(cardQuestion("Why us?", textCard("t1")));
    document.querySelector<HTMLInputElement>(`[name="${nameOf("t1")}"]`)!.value = "my own hand-written answer";

    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("t1"), "LLM draft", false)).toBe("not_empty");
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("t1"), "LLM draft")).toBe("not_empty");
    expect(document.querySelector<HTMLInputElement>(`[name="${nameOf("t1")}"]`)!.value).toBe("my own hand-written answer");
  });

  it("force cannot make a D6-sensitive field fillable", () => {
    buildCards(cardQuestion("What is your gender identity?", textCard("s1")));
    const input = document.querySelector<HTMLInputElement>(`[name="${nameOf("s1")}"]`)!;
    input.value = "already has text, so force would matter if the sensitive check didn't run first";

    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("s1"), "any text", true)).toBe("refused");
    expect(input.value).toBe("already has text, so force would matter if the sensitive check didn't run first");
  });

  it("force cannot make an unmatched/out-of-namespace selector fillable", () => {
    buildCards(cardQuestion("Why us?", textCard("t1")));
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, "eeo[gender]", "text", true)).toBe("refused");
    expect(fillCustomTextAnswer(document, TEST_LEVER_MAP, nameOf("does-not-exist"), "text", true)).toBe("refused");
  });
});

describe("E6: labels are scraped safely (Lever)", () => {
  it("strips control, zero-width and bidi characters from a label", () => {
    buildCards(cardQuestion(`Why us?${RLO} gnorw ${LRI}${BEL} Verified${ZWSP}`, textCard("t1")));
    const q = extractCustomQuestions(document, TEST_LEVER_MAP).find((x) => x.fieldName === nameOf("t1"));
    expect(q?.label).toBe("Why us? gnorw Verified");
  });

  it("caps an oversized label", () => {
    buildCards(cardQuestion("x".repeat(5_000), textCard("t1")));
    const q = extractCustomQuestions(document, TEST_LEVER_MAP).find((x) => x.fieldName === nameOf("t1"));
    expect(q?.label?.length).toBeLessThanOrEqual(300);
  });
});
