import { describe, expect, it } from "vitest";
import { extractCustomQuestions, fillCustomTextAnswer, GENERIC_FIELD_DEFAULTS, isGreenhouseApplyForm } from "@/lib/greenhouse";
import { applyReactControlledFillPlan, planStandardFieldFills } from "@/lib/standardFields";
import type { ExtensionPersonalInfo } from "@/lib/types";

// E4 -- mirrors the real DOM structure confirmed live against three real
// job-boards.greenhouse.io postings (Melio, Wheely, Cloudflare,
// 2026-09-13): standard fields by stable platform id (first_name/
// last_name/email/phone/resume/cover_letter), a genuine `question_<id>`
// custom question with a real `label[for=...]` association, a react-
// select combobox custom question (role="combobox"), and BOTH real EEO/
// demographic containers (`#demographic-section`'s bare-numeric-id
// fields, `.eeoc__container`'s semantic-id fields) -- neither of which
// must ever be treated as a custom question or fillable field.
function buildGreenhouseForm(): void {
  document.body.innerHTML = `
    <form id="application-form">
      <label for="first_name">First Name*</label>
      <input type="text" id="first_name" />
      <label for="last_name">Last Name*</label>
      <input type="text" id="last_name" />
      <label for="email">Email*</label>
      <input type="text" id="email" />
      <label for="phone">Phone*</label>
      <input type="tel" id="phone" />
      <input type="file" id="resume" />
      <input type="file" id="cover_letter" />

      <label for="question_29077808003">LinkedIn Profile</label>
      <input type="text" id="question_29077808003" />

      <label for="question_29077810003">Are you eligible to work in the US?*</label>
      <input type="text" id="question_29077810003" role="combobox" />

      <div id="demographic-section" class="demographic--container">
        <label for="4012698003">How would you describe your gender identity?</label>
        <input type="text" id="4012698003" />
        <!-- A hypothetical org-side edge case: even if something inside this
             container carried the question_ id shape, the container
             exclusion (defense-in-depth on top of the id-prefix rule) must
             still catch it. -->
        <label for="question_999">sneaky</label>
        <input type="text" id="question_999" />
      </div>

      <div class="eeoc__container">
        <label for="gender">Gender</label>
        <input type="text" id="gender" />
      </div>
    </form>
  `;
}

const FULL_INFO: ExtensionPersonalInfo = {
  name: "Jane Middle Doe",
  email: "jane@example.com",
  phone: "+1-555-0100",
  location: { city: "New York", region: "NY", country: "US" },
  linkedin: "https://linkedin.com/in/jane",
  github: "",
  portfolio: "https://jane.dev",
};

describe("isGreenhouseApplyForm", () => {
  it("detects a real Greenhouse apply form by its first_name/resume inputs", () => {
    buildGreenhouseForm();
    expect(isGreenhouseApplyForm(document)).toBe(true);
  });

  it("returns false on an unrelated page", () => {
    document.body.innerHTML = "<div>Not an application form</div>";
    expect(isGreenhouseApplyForm(document)).toBe(false);
  });
});

describe("standard field fill (React-controlled)", () => {
  it("splits the one full-name profile field into first_name/last_name via the firstNameWord/lastNameWord strategies", () => {
    buildGreenhouseForm();
    const plan = planStandardFieldFills(document, FULL_INFO, false, GENERIC_FIELD_DEFAULTS.standardFields);
    const bySelector = Object.fromEntries(plan.map((p) => [p.selector, p.value]));

    expect(bySelector["#first_name"]).toBe("Jane");
    expect(bySelector["#last_name"]).toBe("Middle Doe");
    expect(bySelector["#email"]).toBe("jane@example.com");
    expect(bySelector["#phone"]).toBe("+1-555-0100");
  });

  it("applyReactControlledFillPlan sets values and dispatches input/change a page's own JS can observe", () => {
    buildGreenhouseForm();
    const firstNameInput = document.querySelector<HTMLInputElement>("#first_name")!;
    const events: string[] = [];
    firstNameInput.addEventListener("input", () => events.push("input"));
    firstNameInput.addEventListener("change", () => events.push("change"));

    const filled = applyReactControlledFillPlan(document, [{ selector: "#first_name", value: "Jane" }]);

    expect(firstNameInput.value).toBe("Jane");
    expect(events).toEqual(["input", "change"]);
    expect(filled).toEqual(["#first_name"]);
  });

  it("skips a field the user already filled in, by default (D5)", () => {
    buildGreenhouseForm();
    document.querySelector<HTMLInputElement>("#first_name")!.value = "Already Typed";
    const plan = planStandardFieldFills(document, FULL_INFO, false, GENERIC_FIELD_DEFAULTS.standardFields);
    expect(plan.some((p) => p.selector === "#first_name")).toBe(false);
  });
});

describe("extractCustomQuestions", () => {
  it("extracts a genuine question_<id> field with its real label", () => {
    buildGreenhouseForm();
    const questions = extractCustomQuestions(document);
    const linkedin = questions.find((q) => q.fieldName === "question_29077808003");
    expect(linkedin?.label).toBe("LinkedIn Profile");
    expect(linkedin?.kind).toBe("text");
  });

  it("tags a react-select combobox question (role=combobox) as kind 'radio', never 'text'", () => {
    buildGreenhouseForm();
    const questions = extractCustomQuestions(document);
    const eligibility = questions.find((q) => q.fieldName === "question_29077810003");
    expect(eligibility?.kind).toBe("radio");
  });

  it("never treats a bare-numeric-id demographic-section field as a custom question (D6, by id-prefix construction)", () => {
    buildGreenhouseForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName === "4012698003")).toBe(false);
  });

  it("never treats a classic eeoc__container semantic field as a custom question (D6)", () => {
    buildGreenhouseForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName === "gender")).toBe(false);
  });

  it("excludes a question_<id>-shaped field sitting inside the demographic container -- defense-in-depth beyond the id-prefix rule alone", () => {
    buildGreenhouseForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName === "question_999")).toBe(false);
  });

  it("excludes the caller-identified excludeFieldName", () => {
    buildGreenhouseForm();
    const questions = extractCustomQuestions(document, "question_29077808003");
    expect(questions.some((q) => q.fieldName === "question_29077808003")).toBe(false);
  });
});

describe("fillCustomTextAnswer", () => {
  it("fills a genuine question_<id> text field via the React-controlled setter and dispatches events", () => {
    buildGreenhouseForm();
    const input = document.querySelector<HTMLInputElement>("#question_29077808003")!;
    const events: string[] = [];
    input.addEventListener("input", () => events.push("input"));
    input.addEventListener("change", () => events.push("change"));

    const filled = fillCustomTextAnswer(document, "question_29077808003", "linkedin.com/in/jane");

    expect(filled).toBe("filled");
    expect(input.value).toBe("linkedin.com/in/jane");
    expect(events).toEqual(["input", "change"]);
  });

  it("refuses a field name outside the question_<id> namespace, e.g. a demographic field, without even querying for it", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "4012698003", "x")).toBe("refused");
    expect(fillCustomTextAnswer(document, "gender", "x")).toBe("refused");
  });

  it("refuses a question_<id> field sitting inside the demographic container", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "question_999", "x")).toBe("refused");
  });

  it("refuses a react-select combobox question -- never a plain text fill", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "question_29077810003", "Yes")).toBe("refused");
  });

  it("refuses the standard resume/first_name fields even if somehow asked to fill them", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "resume", "not applicable")).toBe("refused");
    expect(fillCustomTextAnswer(document, "first_name", "not applicable")).toBe("refused");
  });

  it("returns false for a field id that doesn't exist on the page", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "question_00000000", "x")).toBe("refused");
  });
});

// ---------------------------------------------------------------------------
// E6 hardening. `question_<id>` is the TENANT's namespace, not Greenhouse's:
// real boards author "Gender" and "Pronouns" as ordinary custom questions
// outside both EEO containers (Airbnb, Figma), and anyone can create a
// Greenhouse account.
// ---------------------------------------------------------------------------

function buildQuestions(...questions: string[]): void {
  document.body.innerHTML = `<form id="application-form"><input type="file" id="resume" />${questions.join("\n")}</form>`;
}

function q(id: number, label: string | null, control: string): string {
  const labelHtml = label === null ? "" : `<label for="question_${id}">${label}</label>`;
  return `${labelHtml}${control}`;
}

const textInput = (id: number) => `<input type="text" id="question_${id}" />`;

describe("E6: control classification fails closed (Greenhouse)", () => {
  it("lists a native <select> as human-only, never as free text", () => {
    buildQuestions(q(3, "Are you legally authorized to work in the US?", `<select id="question_3"><option>Yes</option></select>`));
    const found = extractCustomQuestions(document).find((x) => x.fieldName === "question_3");
    expect(found?.label).toBe("Are you legally authorized to work in the US?");
    expect(found?.kind).toBe("radio");
  });

  it("lists a wrapper <div> and a <fieldset> carrying a question_ id as human-only", () => {
    buildQuestions(
      q(4, "Fake", `<div id="question_4"></div>`),
      `<fieldset id="question_21"><input type="checkbox" /></fieldset>`,
    );
    const kinds = Object.fromEntries(extractCustomQuestions(document).map((x) => [x.fieldName, x.kind]));
    expect(kinds["question_4"]).toBe("radio");
    expect(kinds["question_21"]).toBe("radio");
  });

  it.each(["date", "number", "password"])("lists an <input type=%s> as human-only", (type) => {
    buildQuestions(q(5, "Earliest start date", `<input type="${type}" id="question_5" />`));
    expect(extractCustomQuestions(document).find((x) => x.fieldName === "question_5")?.kind).toBe("radio");
  });

  it("still lists a plain text input and a textarea as text", () => {
    buildQuestions(q(6, "Why us?", textInput(6)), q(7, "Tell us more", `<textarea id="question_7"></textarea>`));
    const kinds = Object.fromEntries(extractCustomQuestions(document).map((x) => [x.fieldName, x.kind]));
    expect(kinds["question_6"]).toBe("text");
    expect(kinds["question_7"]).toBe("text");
  });

  it("never writes to a select or a wrapper element even when asked directly", () => {
    buildQuestions(
      q(3, "Work authorization?", `<select id="question_3"><option>Yes</option></select>`),
      q(4, "Wrapper", `<div id="question_4"></div>`),
    );
    expect(fillCustomTextAnswer(document, "question_3", "Yes")).toBe("refused");
    expect(fillCustomTextAnswer(document, "question_4", "Yes")).toBe("refused");
  });
});

describe("E6: D6 label check on tenant-authored question_ fields (Greenhouse)", () => {
  // The first two are the real Airbnb and Figma phrasings from their public
  // boards-api question lists; the rest are the adversarial reviewers'.
  const SENSITIVE = [
    "Gender",
    "Pronouns",
    "Preferred pronouns",
    "What is your gender identity?",
    "Do you require any accommodations to complete the interview process?",
    "Are you a veteran?",
    "Do you have a disability? Please describe any accommodations you require.",
    "What is your ethnic background?",
  ];

  for (const label of SENSITIVE) {
    it(`excludes ${JSON.stringify(label)} whether it is an input or a textarea, and refuses to fill it`, () => {
      buildQuestions(q(101, label, textInput(101)), q(102, label, `<textarea id="question_102"></textarea>`));
      const listed = extractCustomQuestions(document).map((x) => x.fieldName);
      expect(listed).not.toContain("question_101");
      expect(listed).not.toContain("question_102");

      expect(fillCustomTextAnswer(document, "question_101", "any text")).toBe("refused");
      expect(fillCustomTextAnswer(document, "question_102", "any text")).toBe("refused");
      expect(document.querySelector<HTMLInputElement>("#question_101")!.value).toBe("");
      expect(document.querySelector<HTMLTextAreaElement>("#question_102")!.value).toBe("");
    });
  }

  it("does NOT exclude a free-text work-authorization question -- a maintainer decision, pinned here", () => {
    buildQuestions(q(110, "Will you now or in the future require sponsorship?", textInput(110)));
    expect(extractCustomQuestions(document).find((x) => x.fieldName === "question_110")?.kind).toBe("text");
    expect(fillCustomTextAnswer(document, "question_110", "No")).toBe("filled");
  });
});

describe("E6: an unreadable label is human-only (Greenhouse)", () => {
  it("downgrades a label-less text field to kind 'radio' and refuses to fill it", () => {
    buildQuestions(q(8, null, textInput(8)));
    const found = extractCustomQuestions(document).find((x) => x.fieldName === "question_8");
    expect(found?.label).toBeNull();
    expect(found?.kind).toBe("radio");
    expect(fillCustomTextAnswer(document, "question_8", "text")).toBe("refused");
  });
});

describe("E6: D5 -- a per-question fill never overwrites text already on the page (Greenhouse)", () => {
  it("returns 'not_empty' and leaves a hand-typed answer untouched", () => {
    buildQuestions(q(9, "Why us?", `<textarea id="question_9"></textarea>`));
    const area = document.querySelector<HTMLTextAreaElement>("#question_9")!;
    area.value = "my own hand-written answer";
    const events: string[] = [];
    area.addEventListener("input", () => events.push("input"));

    expect(fillCustomTextAnswer(document, "question_9", "LLM draft")).toBe("not_empty");
    expect(area.value).toBe("my own hand-written answer");
    expect(events).toEqual([]);
  });

  it("treats whitespace-only content as empty", () => {
    buildQuestions(q(9, "Why us?", textInput(9)));
    document.querySelector<HTMLInputElement>("#question_9")!.value = "  ";
    expect(fillCustomTextAnswer(document, "question_9", "LLM draft")).toBe("filled");
  });
});

// ---------------------------------------------------------------------------
// E6 continuation -- the per-question "Replace" action's `force` parameter.
// Mirrors lever.test.ts's own force suite: proves force bypasses ONLY D5,
// never the D6 sensitive-label or namespace/container refusals.
// ---------------------------------------------------------------------------

describe("E6 continuation: fillCustomTextAnswer's force parameter (Greenhouse)", () => {
  it("force:true overwrites existing text", () => {
    buildQuestions(q(9, "Why us?", textInput(9)));
    document.querySelector<HTMLInputElement>("#question_9")!.value = "my own hand-written answer";

    expect(fillCustomTextAnswer(document, "question_9", "Replacement draft", true)).toBe("filled");
    expect(document.querySelector<HTMLInputElement>("#question_9")!.value).toBe("Replacement draft");
  });

  it("force:false (the default) still never overwrites -- the existing D5 behavior is unchanged", () => {
    buildQuestions(q(9, "Why us?", textInput(9)));
    document.querySelector<HTMLInputElement>("#question_9")!.value = "my own hand-written answer";

    expect(fillCustomTextAnswer(document, "question_9", "LLM draft", false)).toBe("not_empty");
    expect(fillCustomTextAnswer(document, "question_9", "LLM draft")).toBe("not_empty");
    expect(document.querySelector<HTMLInputElement>("#question_9")!.value).toBe("my own hand-written answer");
  });

  it("force cannot make a D6-sensitive field fillable", () => {
    buildQuestions(q(101, "What is your gender identity?", textInput(101)));
    const input = document.querySelector<HTMLInputElement>("#question_101")!;
    input.value = "already has text";

    expect(fillCustomTextAnswer(document, "question_101", "any text", true)).toBe("refused");
    expect(input.value).toBe("already has text");
  });

  it("force cannot make a field inside the EEO/demographic container fillable", () => {
    buildGreenhouseForm();
    // question_999 sits inside #demographic-section -- see buildGreenhouseForm's
    // own comment: even a question_-shaped id there must stay refused.
    expect(fillCustomTextAnswer(document, "question_999", "any text", true)).toBe("refused");
  });

  it("force cannot make an unmatched/nonexistent field fillable", () => {
    buildQuestions(q(9, "Why us?", textInput(9)));
    expect(fillCustomTextAnswer(document, "question_00000000", "text", true)).toBe("refused");
    expect(fillCustomTextAnswer(document, "gender", "text", true)).toBe("refused");
  });
});
