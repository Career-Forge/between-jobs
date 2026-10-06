import { afterEach, describe, expect, it, vi } from "vitest";
import {
  extractCustomQuestions,
  fillCountryField,
  fillCustomTextAnswer,
  GENERIC_FIELD_DEFAULTS,
  isGreenhouseApplyForm,
} from "@/lib/greenhouse";
import { applyReactControlledFillPlan, planStandardFieldFills } from "@/lib/standardFields";
import type { ExtensionPersonalInfo } from "@/lib/types";
import { watchInteractions } from "./helpers/neverSubmit";

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

    // "Jane Middle Doe": everything before the family name is given names (lib/personName.ts).
    expect(bySelector["#first_name"]).toBe("Jane Middle");
    expect(bySelector["#last_name"]).toBe("Doe");
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


// ---- #country: a list, not a text box ------------------------------------------------------
//
// Synthetic fixtures only. The react-select below is a hand-written imitation of what the
// board renders (an input[role=combobox] in a container holding the chosen value, and a
// listbox of options that appears once text is typed). It proves this engine's own logic
// against that shape; it cannot prove the real library reacts to the same typing and click.

const FAST = { timeoutMs: 120, pollMs: 5 };
const COUNTRIES = ["United States", "United States Minor Outlying Islands", "India", "Germany", "United Kingdom"];

interface ComboboxOptions {
  chosen?: string | null;
  /** The list ignores typing (nothing opens). */
  deaf?: boolean;
  /** Clicking an option does nothing. */
  clickDoesNothing?: boolean;
  options?: string[];
}

function buildCombobox(opts: ComboboxOptions = {}): { input: HTMLInputElement; clicked: HTMLElement[] } {
  const { chosen = null, deaf = false, clickDoesNothing = false, options = COUNTRIES } = opts;
  document.body.innerHTML = `
    <form id="application-form">
      <label id="country-label" for="country">Country</label>
      <div class="select__container">
        <div class="select__control">
          <div class="select__value-container">
            ${chosen === null ? '<div class="select__placeholder">Select...</div>' : `<div class="select__single-value">${chosen}</div>`}
            <div class="select__input-container">
              <input id="country" class="select__input" type="text" role="combobox" aria-controls="react-select-country-listbox" aria-expanded="false" />
            </div>
          </div>
        </div>
      </div>
    </form>`;
  const input = document.querySelector<HTMLInputElement>("#country")!;
  const container = document.querySelector(".select__container")!;
  const clicked: HTMLElement[] = [];
  input.addEventListener("input", () => {
    container.querySelector("#react-select-country-listbox")?.remove();
    if (deaf || input.value === "") return;
    const listbox = document.createElement("div");
    listbox.id = "react-select-country-listbox";
    listbox.setAttribute("role", "listbox");
    for (const name of options.filter((o) => o.toLowerCase().includes(input.value.toLowerCase()))) {
      const option = document.createElement("div");
      option.setAttribute("role", "option");
      option.textContent = name;
      option.addEventListener("click", () => {
        clicked.push(option);
        if (clickDoesNothing) return;
        container.querySelector(".select__single-value, .select__placeholder")?.replaceWith(
          Object.assign(document.createElement("div"), { className: "select__single-value", textContent: name }),
        );
        listbox.remove();
        input.value = "";
      });
      listbox.append(option);
    }
    container.append(listbox);
  });
  return { input, clicked };
}

const shown = () => document.querySelector(".select__single-value")?.textContent ?? null;

describe("#country as the standard react-select combobox", () => {
  afterEach(() => vi.restoreAllMocks());

  it("types the country, picks the matching option, and confirms the choice took", async () => {
    const { clicked } = buildCombobox();

    const outcome = await fillCountryField(document, "US", false, FAST);

    expect(outcome).toEqual({ status: "filled" });
    expect(shown()).toBe("United States");
    // The one thing activated is an option of the list it just filtered.
    expect(clicked).toHaveLength(1);
    expect(clicked[0]!.getAttribute("role")).toBe("option");
  });

  it("only an exact name is taken: 'United States Minor Outlying Islands' is not the United States", async () => {
    buildCombobox({ options: ["United States Minor Outlying Islands", "India"] });

    const outcome = await fillCountryField(document, "US", false, FAST);

    expect(outcome.status).toBe("not_filled");
    expect(shown()).toBeNull();
  });

  it("a country no option matches is left empty with a reason, and the half-typed search is cleared", async () => {
    const { input, clicked } = buildCombobox();

    const outcome = await fillCountryField(document, "Atlantis", false, FAST);

    expect(outcome).toEqual({
      status: "not_filled",
      reason: "none of the form's country options matches the country in your profile",
    });
    expect(input.value).toBe("");
    expect(clicked).toEqual([]);
  });

  it("a list that never reacts is reported as 'check this field', never as filled", async () => {
    const { input, clicked } = buildCombobox({ deaf: true });

    const outcome = await fillCountryField(document, "US", false, FAST);

    expect(outcome.status).toBe("not_filled");
    expect(outcome.status === "not_filled" && outcome.reason).toMatch(/check this field/);
    expect(input.value).toBe("");
    expect(clicked).toEqual([]);
  });

  it("an option click the list ignores is not reported as a fill", async () => {
    buildCombobox({ clickDoesNothing: true });

    const outcome = await fillCountryField(document, "US", false, FAST);

    expect(outcome.status).toBe("not_filled");
    expect(outcome.status === "not_filled" && outcome.reason).toMatch(/check this field/);
    expect(shown()).toBeNull();
  });

  it("D5: a country already chosen is left alone; refill-all replaces it", async () => {
    const { clicked } = buildCombobox({ chosen: "India" });

    expect(await fillCountryField(document, "US", false, FAST)).toEqual({ status: "left" });
    expect(shown()).toBe("India");
    expect(clicked).toEqual([]);

    expect(await fillCountryField(document, "US", true, FAST)).toEqual({ status: "filled" });
    expect(shown()).toBe("United States");
  });

  it("a dotted spelling is typed as the country's name: 'U.S.' finds the United States", async () => {
    buildCombobox();

    expect(await fillCountryField(document, "U.S.", false, FAST)).toEqual({ status: "filled" });
    expect(shown()).toBe("United States");
  });

  it("matches a country written out as well as a code or alias", async () => {
    for (const written of ["Germany", "germany", "DE", "UK", "USA"]) {
      buildCombobox();
      const outcome = await fillCountryField(document, written, false, FAST);
      expect(outcome, written).toEqual({ status: "filled" });
    }
    buildCombobox();
    await fillCountryField(document, "UK", false, FAST);
    expect(shown()).toBe("United Kingdom");
  });

  it("a throw anywhere in the interaction is a reported failure, not an exception", async () => {
    const { input } = buildCombobox();
    input.focus = () => {
      throw new Error("focus blew up");
    };

    const outcome = await fillCountryField(document, "US", false, FAST);

    expect(outcome.status).toBe("not_filled");
  });
});

describe("#country as a plain <select>", () => {
  function buildSelect(selectedValue = ""): HTMLSelectElement {
    document.body.innerHTML = `
      <form><select id="country">
        <option value="">Select...</option>
        <option value="US">United States</option>
        <option value="IN">India</option>
        <option value="GB">United Kingdom</option>
        <option value="DE">Germany</option>
      </select></form>`;
    const select = document.querySelector<HTMLSelectElement>("#country")!;
    select.value = selectedValue;
    return select;
  }

  it("chooses the matching option and fires the events a controlled form listens for", async () => {
    const select = buildSelect();
    const events: string[] = [];
    select.addEventListener("input", () => events.push("input"));
    select.addEventListener("change", () => events.push("change"));

    expect(await fillCountryField(document, "US", false, FAST)).toEqual({ status: "filled" });

    expect(select.value).toBe("US");
    expect(events).toEqual(["input", "change"]);
  });

  it("matches by the option's text or its value, and a country written out", async () => {
    for (const [profile, value] of [["IN", "IN"], ["india", "IN"], ["Germany", "DE"], ["United Kingdom", "GB"]] as const) {
      const select = buildSelect();
      expect(await fillCountryField(document, profile, false, FAST), profile).toEqual({ status: "filled" });
      expect(select.value).toBe(value);
    }
  });

  it("a dotted spelling of a country is matched: 'U.S.' is the United States", async () => {
    for (const written of ["U.S.", "U.S", "u.s.", "U.K."]) {
      const select = buildSelect();
      expect(await fillCountryField(document, written, false, FAST), written).toEqual({ status: "filled" });
      expect(select.value, written).toBe(written.toLowerCase().startsWith("u.k") ? "GB" : "US");
    }
  });

  it("a choice the form does not take is reported, never counted as filled", async () => {
    const select = buildSelect();
    // A controlled form that reverts whatever is chosen.
    select.addEventListener("change", () => {
      select.selectedIndex = 0;
    });

    const outcome = await fillCountryField(document, "US", false, FAST);

    expect(outcome.status).toBe("not_filled");
    expect(outcome.status === "not_filled" && outcome.reason).toMatch(/did not take the choice/);
  });

  it("D5: a real choice is left alone, a placeholder is not; refill-all replaces a real one", async () => {
    const select = buildSelect("IN");
    expect(await fillCountryField(document, "US", false, FAST)).toEqual({ status: "left" });
    expect(select.value).toBe("IN");
    expect(await fillCountryField(document, "US", true, FAST)).toEqual({ status: "filled" });
    expect(select.value).toBe("US");
  });

  it("a country with no matching option leaves the list as it was, with a reason", async () => {
    const select = buildSelect();
    const outcome = await fillCountryField(document, "Atlantis", false, FAST);
    expect(outcome.status).toBe("not_filled");
    expect(select.value).toBe("");
  });
});

describe("#country: what is not a list, and what is missing", () => {
  it("a form without #country has nothing to say", async () => {
    document.body.innerHTML = '<form><input type="text" id="first_name" /></form>';
    expect(await fillCountryField(document, "US", false, FAST)).toEqual({ status: "absent" });
  });

  it("a profile with no country leaves the field alone and says so", async () => {
    buildCombobox();
    expect(await fillCountryField(document, "  ", false, FAST)).toEqual({
      status: "not_filled",
      reason: "your profile has no country",
    });
  });

  it("a #country that is neither a select nor a combobox is not touched", async () => {
    document.body.innerHTML = '<form><input type="text" id="country" /></form>';
    const outcome = await fillCountryField(document, "US", false, FAST);
    expect(outcome.status).toBe("not_filled");
    expect(document.querySelector<HTMLInputElement>("#country")!.value).toBe("");
  });

  it("is not part of the text-entry standard fields (those never write to a list)", () => {
    expect(GENERIC_FIELD_DEFAULTS.standardFields.map((f) => f.selector)).not.toContain("#country");
  });
});

// ---- #country: the page's markup is untrusted ------------------------------------------------
//
// A job page decides its own markup. The one click this engine makes must land only on a plain
// entry of a country list, never on a submit button, a link, a label that forwards to a box, or
// anything else a page labels `role=option` or points `aria-controls` at.

describe("#country: a page cannot steer the one click onto a control", () => {
  const COMBOBOX = (controls: string | null) => `
    <div class="select__container"><div class="select__control"><div class="select__value-container">
      <div class="select__placeholder">Select...</div>
      <div class="select__input-container">
        <input id="country" class="select__input" type="text" role="combobox"${controls === null ? "" : ` aria-controls="${controls}"`} />
      </div>
    </div></div></div>`;

  interface Probe {
    submits: number;
    linkClicks: number;
    consent: HTMLInputElement;
  }

  /** `body` is the page markup around (and holding) the hostile listbox. */
  function build(body: string): Probe {
    document.body.innerHTML = `
      <form id="application-form">
        ${body}
        <input type="checkbox" id="consent" /><button type="button" id="create">Create account</button>
      </form>`;
    const probe: Probe = { submits: 0, linkClicks: 0, consent: document.querySelector<HTMLInputElement>("#consent")! };
    document.querySelector("form")!.addEventListener("submit", (event) => {
      probe.submits += 1;
      event.preventDefault();
    });
    for (const link of document.querySelectorAll("a")) {
      link.addEventListener("click", (event) => {
        probe.linkClicks += 1;
        event.preventDefault();
      });
    }
    return probe;
  }

  const HOSTILE: Array<[string, string]> = [
    [
      "a submit button called an option, inside the combobox's own list",
      `${COMBOBOX("lb")}<div id="lb" role="listbox"><button type="submit" role="option">United States</button></div>`,
    ],
    [
      "a link called an option, in a list elsewhere on the page that aria-controls points at",
      `${COMBOBOX("lb")}<nav><div id="lb" role="listbox"><a href="/elsewhere" role="option">United States</a></div></nav>`,
    ],
    [
      "a link nested one level down in a plain div",
      `${COMBOBOX("lb")}<div id="lb" role="listbox"><div><a href="/x" role="option">United States</a></div></div>`,
    ],
    [
      "a plain option inside a submit button inside the list",
      `${COMBOBOX("lb")}<div id="lb" role="listbox"><button type="submit"><span role="option">United States</span></button></div>`,
    ],
    [
      "a plain option inside a label that ticks the consent box",
      `${COMBOBOX("lb")}<div id="lb" role="listbox"><label for="consent"><span role="option">United States</span></label></div>`,
    ],
    [
      "a plain option inside a label that wraps the whole list",
      `${COMBOBOX("lb")}<label for="consent"><div id="lb" role="listbox"><div role="option">United States</div></div></label>`,
    ],
    [
      "a list wrapped in a link",
      `${COMBOBOX("lb")}<a href="/x"><div id="lb" role="listbox"><div role="option">United States</div></div></a>`,
    ],
    [
      "no aria-controls: the list found by looking around the input holds a submit button",
      `<div class="select__container"><div class="select__control"><div class="select__value-container">
         <div class="select__input-container"><input id="country" type="text" role="combobox" /></div>
       </div></div><div role="listbox"><button type="submit" role="option">United States</button></div></div>`,
    ],
    [
      "an option dressed as a button by its own role",
      `${COMBOBOX("lb")}<div id="lb" role="listbox"><div role="button"><div role="option">United States</div></div></div>`,
    ],
  ];

  it.each(HOSTILE)("%s: nothing is clicked, submitted or ticked, and the field is reported as not filled", async (_name, body) => {
    const probe = build(body);
    const watcher = watchInteractions();
    try {
      const outcome = await fillCountryField(document, "US", false, FAST);

      expect(outcome.status).toBe("not_filled");
      expect(probe.submits).toBe(0);
      expect(probe.linkClicks).toBe(0);
      expect(probe.consent.checked).toBe(false);
      expect(watcher.interactions.filter((i) => i.kind === "click")).toEqual([]);
      expect(watcher.violations().filter((v) => v.kind !== "focus")).toEqual([]);
    } finally {
      watcher.restore();
    }
  });

  it("aria-controls pointing at an element that is not a listbox is not trusted", async () => {
    const probe = build(`${COMBOBOX("decoy")}<div id="decoy"><div role="option">United States</div></div>`);
    const watcher = watchInteractions();
    try {
      const outcome = await fillCountryField(document, "US", false, FAST);
      expect(outcome.status).toBe("not_filled");
      expect(watcher.interactions.filter((i) => i.kind === "click")).toEqual([]);
      expect(probe.submits).toBe(0);
    } finally {
      watcher.restore();
    }
  });

  it("the control: a plain option in a plain list, even one built from list items, is still picked", async () => {
    build(`${COMBOBOX("lb")}`);
    const container = document.querySelector(".select__container")!;
    const input = document.querySelector<HTMLInputElement>("#country")!;
    const clicked: string[] = [];
    input.addEventListener("input", () => {
      container.querySelector("#lb")?.remove();
      if (input.value === "") return;
      const list = document.createElement("ul");
      list.id = "lb";
      list.setAttribute("role", "listbox");
      const item = document.createElement("li");
      item.setAttribute("role", "option");
      item.textContent = "United States";
      item.addEventListener("click", () => {
        clicked.push("li");
        container.querySelector(".select__placeholder")?.replaceWith(
          Object.assign(document.createElement("div"), { className: "select__single-value", textContent: "United States" }),
        );
        list.remove();
      });
      list.append(item);
      container.append(list);
    });
    const watcher = watchInteractions();
    try {
      expect(await fillCountryField(document, "US", false, FAST)).toEqual({ status: "filled" });
      expect(clicked).toEqual(["li"]);
      expect(watcher.violations().filter((v) => v.kind !== "focus")).toEqual([]);
    } finally {
      watcher.restore();
    }
  });
});
