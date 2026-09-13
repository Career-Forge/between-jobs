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

    expect(filled).toBe(true);
    expect(input.value).toBe("linkedin.com/in/jane");
    expect(events).toEqual(["input", "change"]);
  });

  it("refuses a field name outside the question_<id> namespace, e.g. a demographic field, without even querying for it", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "4012698003", "x")).toBe(false);
    expect(fillCustomTextAnswer(document, "gender", "x")).toBe(false);
  });

  it("refuses a question_<id> field sitting inside the demographic container", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "question_999", "x")).toBe(false);
  });

  it("refuses a react-select combobox question -- never a plain text fill", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "question_29077810003", "Yes")).toBe(false);
  });

  it("refuses the standard resume/first_name fields even if somehow asked to fill them", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "resume", "not applicable")).toBe(false);
    expect(fillCustomTextAnswer(document, "first_name", "not applicable")).toBe(false);
  });

  it("returns false for a field id that doesn't exist on the page", () => {
    buildGreenhouseForm();
    expect(fillCustomTextAnswer(document, "question_00000000", "x")).toBe(false);
  });
});
