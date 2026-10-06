import { afterEach, describe, expect, it } from "vitest";
import * as ashby from "@/lib/ashby";
import * as greenhouse from "@/lib/greenhouse";
import * as lever from "@/lib/lever";
import { isSensitiveSelfIdText } from "@/lib/questionSafety";
import type { FillResult } from "@/lib/types";
import { LEVER_MAP, loadContentScript, trackedState } from "./helpers/contentHarness";

// D6, the backstop. Voluntary self-identification questions -- gender, race, disability,
// veteran status, sexual orientation, religion, age, marital status -- are never drafted by a
// model and never filled by this extension, on any ATS, however the organisation spells or
// places them. This is the table that has to stay true: each label/value pair is a question a
// form could ask and the answer a profile or a model could offer. The classifier must say it
// is sensitive; the extraction must not list it (so the panel can never offer to draft it);
// and a fill aimed at it directly, even a forced one, must write nothing.
//
// tests/test_extension_routes.py holds the server-side half of the same table (the draft route
// must decline each without calling a model); the two lists are meant to stay in step.

type Pair = [category: string, label: string, value: string];

export const SELF_ID_PAIRS: Pair[] = [
  ["gender", "What is your gender identity?", "Female"],
  ["gender", "Gender", "Non-binary"],
  ["gender", "Preferred pronouns", "she/her"],
  ["gender", "Do you identify as transgender?", "No"],
  ["race / ethnicity", "Race / Ethnicity", "Asian"],
  ["race / ethnicity", "Are you Hispanic or Latino?", "No"],
  ["race / ethnicity", "How would you describe your ethnic background?", "South Asian"],
  ["disability", "Do you have a disability?", "No"],
  ["disability", "Disability status", "Prefer not to say"],
  ["disability", "Do you require any accommodations during the interview process?", "No"],
  ["veteran status", "Are you a protected veteran?", "No"],
  ["veteran status", "Veteran status", "I am not a veteran"],
  ["veteran status", "Have you served in the armed forces?", "No"],
  ["sexual orientation", "Sexual orientation", "Heterosexual"],
  ["sexual orientation", "Do you identify as LGBTQ+?", "No"],
  ["religion", "What is your religion?", "Hindu"],
  ["religion", "Religious affiliation", "None"],
  ["age", "What is your age?", "34"],
  ["age", "Date of birth", "1990-01-01"],
  ["age", "Age range", "30-39"],
  ["marital status", "Marital status", "Married"],
  ["marital status", "Do you have children?", "No"],
  ["marital status", "Number of dependents", "0"],
  // Disguised spellings: invisible characters, full-width letters, look-alike letters.
  ["gender (zero-width space)", "Gen​der", "Female"],
  ["gender (full-width)", "Ｇｅｎｄｅｒ", "Female"],
  ["gender (Cyrillic e)", "Gеnder", "Female"],
  ["race (accents)", "Ràcé and Éthnicity", "Asian"],
  // Each of these terms is pinned by a row of its own: the labels above also match another term
  // ("Race / Ethnicity" matches "ethnic", "Are you Hispanic or Latino?" matches "latin"), so a
  // vocabulary that lost "race", "hispanic", "bisexual" or "identify as" would still pass them.
  ["race / ethnicity", "What is your race?", "Asian"],
  ["race / ethnicity", "Are you of Hispanic origin?", "No"],
  ["sexual orientation", "Are you bisexual?", "No"],
  ["gender", "Do you identify as a member of any community?", "No"],
  // Ordinary wordings of the policy's categories (see tests/questionSafety.test.ts for the table).
  ["veteran status", "Have you served in the military?", "No"],
  ["veteran status", "Vet status", "No"],
  ["disability", "Do you need any adjustments to the interview process?", "No"],
  ["disability", "Do you need support during the interview?", "No"],
  ["age", "What year were you born?", "1990"],
  ["age", "Are you at least 18 years old?", "Yes"],
  ["marital status", "Are you single?", "Yes"],
  ["religion", "Are you Muslim?", "No"],
  ["gender", "Are you intersex?", "No"],
  ["age", "Fecha de nacimiento", "1990-01-01"],
  ["age", "Date de naissance", "1990-01-01"],
  ["age", "Geburtsdatum", "1990-01-01"],
  ["age", "出生日期", "1990-01-01"],
  ["gender", "성별", "Female"],
  ["disability", "장애 여부", "No"],
  ["age", "Возраст", "34"],
  ["marital status", "Семейное положение", "Married"],
  ["age", "العمر", "34"],
];

describe("the self-identification table", () => {
  it("covers every category the policy names", () => {
    const categories = new Set(SELF_ID_PAIRS.map(([category]) => category.split(" (")[0]));
    for (const must of ["gender", "race / ethnicity", "disability", "veteran status", "sexual orientation", "religion", "age", "marital status"]) {
      expect(categories.has(must), must).toBe(true);
    }
  });

  it.each(SELF_ID_PAIRS)("%s: %j is classified as sensitive", (_category, label) => {
    expect(isSensitiveSelfIdText(label)).toBe(true);
  });
});

// ---- on each ATS: never listed, never filled ----------------------------------------------

const lines = (rows: Pair[]) => rows.map(([category, label, value]) => [category, label, value] as const);

describe.each([
  {
    ats: "Greenhouse",
    build: (label: string) => `<form><label for="question_77">${label}</label><input type="text" id="question_77" /></form>`,
    buildArea: (label: string) => `<form><label for="question_77">${label}</label><textarea id="question_77"></textarea></form>`,
    field: "#question_77",
    name: "question_77",
    list: () => greenhouse.extractCustomQuestions(document).map((q) => q.fieldName),
    fill: (name: string, value: string, force: boolean) => greenhouse.fillCustomTextAnswer(document, name, value, force),
  },
  {
    ats: "Lever",
    build: (label: string) =>
      `<form><div><div class="application-label">${label}</div><div class="application-field"><input type="text" name="cards[q9][field0]" /></div></div></form>`,
    buildArea: (label: string) =>
      `<form><div><div class="application-label">${label}</div><div class="application-field"><textarea name="cards[q9][field0]"></textarea></div></div></form>`,
    field: '[name="cards[q9][field0]"]',
    name: "cards[q9][field0]",
    list: () => lever.extractCustomQuestions(document, LEVER_MAP).map((q) => q.fieldName),
    fill: (name: string, value: string, force: boolean) => lever.fillCustomTextAnswer(document, LEVER_MAP, name, value, force),
  },
  {
    ats: "Ashby",
    build: (label: string) =>
      `<div data-field-path="q-9"><label class="ashby-application-form-question-title" for="q-9">${label}</label><input type="text" id="q-9" name="q-9" /></div>`,
    buildArea: (label: string) =>
      `<div data-field-path="q-9"><label class="ashby-application-form-question-title" for="q-9">${label}</label><textarea id="q-9" name="q-9"></textarea></div>`,
    field: "#q-9",
    name: "q-9",
    list: () => ashby.extractCustomQuestions(document).map((q) => q.fieldName),
    fill: (name: string, value: string, force: boolean) => ashby.fillCustomTextAnswer(document, name, value, force),
  },
])("$ats", ({ build, buildArea, field, name, list, fill }) => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it.each(lines(SELF_ID_PAIRS))("%s: %j is never listed and never filled (input)", (_category, label, value) => {
    document.body.innerHTML = build(label);

    expect(list()).toEqual([]);
    expect(fill(name, value, false)).toBe("refused");
    expect(fill(name, value, true)).toBe("refused");
    expect(document.querySelector<HTMLInputElement>(field)!.value).toBe("");
  });

  it.each(lines(SELF_ID_PAIRS))("%s: %j is never listed and never filled (textarea)", (_category, label, value) => {
    document.body.innerHTML = buildArea(label);

    expect(list()).toEqual([]);
    expect(fill(name, value, true)).toBe("refused");
    expect(document.querySelector<HTMLTextAreaElement>(field)!.value).toBe("");
  });

  it("an ordinary question next to them is still listed and filled -- the guard is not a blanket refusal", () => {
    document.body.innerHTML = build("Why do you want to work here?");

    expect(list()).toEqual([name]);
    expect(fill(name, "Because.", false)).toBe("filled");
  });
});

describe("Ashby: the self-ID wording does not have to be in the title", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it.each(lines(SELF_ID_PAIRS.slice(0, 12)))("%s: a neutral title over a self-ID description (%j) stays human-only", (_c, label, value) => {
    document.body.innerHTML = `
      <div class="ashby-application-form-section-container">
        <div data-field-path="q-9">
          <label class="ashby-application-form-question-title" for="q-9">Optional</label>
          <div class="ashby-application-form-question-description">${label}</div>
          <input type="text" id="q-9" name="q-9" />
        </div>
      </div>`;

    expect(ashby.extractCustomQuestions(document)).toEqual([]);
    expect(ashby.fillCustomTextAnswer(document, "q-9", value, true)).toBe("refused");
  });
});

// ---- through the real content script ------------------------------------------------------

describe("the content script, end to end", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  const GREENHOUSE_URL = "https://job-boards.greenhouse.io/acme/jobs/1";
  const respond = (m: { type: string }): unknown =>
    m.type === "PAGE_DETECTED" ? trackedState() : m.type === "VERIFY_SESSION" ? { valid: true } : undefined;

  it("lists none of them for the panel and refuses to write any of them, on a form that also has an ordinary question", async () => {
    const rows = SELF_ID_PAIRS.slice(0, 10);
    document.body.innerHTML = `<form>
      <input type="text" id="first_name" /><input type="text" id="last_name" /><input type="text" id="email" /><input type="file" id="resume" />
      ${rows.map(([, label], i) => `<label for="question_${100 + i}">${label}</label><input type="text" id="question_${100 + i}" />`).join("")}
      <label for="question_1">Why us?</label><textarea id="question_1"></textarea>
    </form>`;
    const harness = await loadContentScript({ href: GREENHOUSE_URL, respond });

    const fill = (await harness.send({ type: "REQUEST_FILL", forceRefillAll: true })) as FillResult;
    expect(fill.unresolvedQuestions.map((q) => q.fieldName)).toEqual(["question_1"]);

    for (const [i, [, , value]] of rows.entries()) {
      const reply = await harness.send({ type: "FILL_FIELD", fieldName: `question_${100 + i}`, value, force: true });
      expect(reply).toEqual({ filled: false, reason: "refused" });
      expect(document.querySelector<HTMLInputElement>(`#question_${100 + i}`)!.value).toBe("");
    }
  });
});
