import { describe, expect, it } from "vitest";
import { extractCustomQuestions, fillCustomTextAnswer, GENERIC_FIELD_DEFAULTS, isAshbyApplyForm } from "@/lib/ashby";
import { applyReactControlledFillPlan, planStandardFieldFills } from "@/lib/standardFields";
import type { ExtensionPersonalInfo } from "@/lib/types";

// E5 -- mirrors the real DOM structure confirmed live against three real
// jobs.ashbyhq.com postings (foundry-for-good, everai, ema, 2026-09-13).
// Deliberately has NO `<form>` element at all -- confirmed live Ashby's
// own apply page never renders one (it submits via its own JS/fetch),
// so every selector this engine uses is scoped to `document`, never a
// form. Real per-org custom questions carry `data-field-path` equal to
// their own canonical UUID on a wrapping "field entry" div, with a
// `.ashby-application-form-question-title` label inside it -- confirmed
// live this is a stable, un-hashed platform class, not per-org styling.
// A radio-group question's own `data-field-path` sits on the SAME kind
// of wrapping div even though its `name`/`id` are compound
// `{sectionId}_{fieldId}` strings shared across every option -- real,
// confirmed live on a genuine "preferred pronouns" question.
function buildAshbyForm(): void {
  document.body.innerHTML = `
    <div class="ashby-application-form-container">
      <input type="file" id="_autofill_helper" />

      <div data-field-path="_systemfield_name" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="_systemfield_name">Name</label>
        <input type="text" id="_systemfield_name" name="_systemfield_name" />
      </div>
      <div data-field-path="_systemfield_email" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="_systemfield_email">Email</label>
        <input type="email" id="_systemfield_email" name="_systemfield_email" />
      </div>
      <div data-field-path="_systemfield_resume" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="_systemfield_resume">Resume</label>
        <input type="file" id="_systemfield_resume" />
      </div>

      <div data-field-path="dd4dc7a2-c59a-463e-94e9-a27b546deb8b" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="dd4dc7a2-c59a-463e-94e9-a27b546deb8b">Phone number</label>
        <input type="tel" id="dd4dc7a2-c59a-463e-94e9-a27b546deb8b" name="dd4dc7a2-c59a-463e-94e9-a27b546deb8b" />
      </div>

      <fieldset data-field-path="d71f0f52-ff84-400e-97ed-e194e17ca8ca" class="ashby-application-form-field-entry ashby-application-form-input-radio-group">
        <label class="ashby-application-form-question-title">What are your preferred pronouns?</label>
        <input type="radio" name="bd7bdce0-e0ca-4dc3-bb51-277db2bcfeac_d71f0f52-ff84-400e-97ed-e194e17ca8ca" id="pronoun-0" value="He/him" />
        <input type="radio" name="bd7bdce0-e0ca-4dc3-bb51-277db2bcfeac_d71f0f52-ff84-400e-97ed-e194e17ca8ca" id="pronoun-1" value="She/her" />
      </fieldset>

      <!-- A benign radio group with the same compound-name shape as the
           pronoun fieldset above, used to test dedup-by-data-field-path
           in isolation now that the pronoun group itself is excluded
           entirely by the D6 fix below (bringing Ashby in line with how
           Greenhouse's own EEOC block is excluded outright, not just
           demoted to kind:"radio"). -->
      <fieldset data-field-path="c2a1e903-aaaa-4b1a-9a1a-000000000099" class="ashby-application-form-field-entry ashby-application-form-input-radio-group">
        <label class="ashby-application-form-question-title">How did you hear about this role?</label>
        <input type="radio" name="9d0f1234-bbbb-4c1a-9a1a-000000000098_c2a1e903-aaaa-4b1a-9a1a-000000000099" id="source-0" value="Referral" />
        <input type="radio" name="9d0f1234-bbbb-4c1a-9a1a-000000000098_c2a1e903-aaaa-4b1a-9a1a-000000000099" id="source-1" value="LinkedIn" />
      </fieldset>

      <!-- D6 fix regression fixture: a hypothetical org phrasing genuine
           demographic self-ID as FREE TEXT rather than a radio group --
           the exact disclosed gap (no structural namespace like Lever/
           Greenhouse have). -->
      <div data-field-path="8f3b1c4d-1111-4a1a-9a1a-000000000001" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="8f3b1c4d-1111-4a1a-9a1a-000000000001">Please describe your gender identity (optional)</label>
        <input type="text" id="8f3b1c4d-1111-4a1a-9a1a-000000000001" name="8f3b1c4d-1111-4a1a-9a1a-000000000001" />
      </div>

      <!-- Deliberately NOT excluded by the same fix: a free-text
           work-authorization question is a different category (see the
           comment above isDemographicSelfIdLabel in lib/ashby.ts) -- it
           stays visible and flows through the existing generate/verify/
           human-review pipeline, same as it would on Greenhouse. -->
      <div data-field-path="8f3b1c4d-2222-4a1a-9a1a-000000000002" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="8f3b1c4d-2222-4a1a-9a1a-000000000002">Please describe your current work authorization status</label>
        <input type="text" id="8f3b1c4d-2222-4a1a-9a1a-000000000002" name="8f3b1c4d-2222-4a1a-9a1a-000000000002" />
      </div>

      <!-- The exact real, live-observed question (jobs.ashbyhq.com/everai,
           2026-09-13) that first surfaced this gap in browser-extension.md
           -- a genuine disability-accommodation question phrased as a
           euphemism, with no word matching "disab" anywhere in it. -->
      <div data-field-path="8f3b1c4d-3333-4a1a-9a1a-000000000003" class="ashby-application-form-field-entry">
        <label class="ashby-application-form-question-title" for="8f3b1c4d-3333-4a1a-9a1a-000000000003">Do you require any accommodations or support during the interview(s)?</label>
        <input type="text" id="8f3b1c4d-3333-4a1a-9a1a-000000000003" name="8f3b1c4d-3333-4a1a-9a1a-000000000003" />
      </div>

      <textarea id="g-recaptcha-response-100000" name="g-recaptcha-response"></textarea>
    </div>
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

describe("isAshbyApplyForm", () => {
  it("detects a real Ashby apply page by its _systemfield_name/_systemfield_resume inputs", () => {
    buildAshbyForm();
    expect(isAshbyApplyForm(document)).toBe(true);
  });

  it("returns false on the posting's own description-only page (no application form yet)", () => {
    document.body.innerHTML = `<div><a href="/application">Apply for this Job</a></div>`;
    expect(isAshbyApplyForm(document)).toBe(false);
  });
});

describe("standard field fill (React-controlled, no <form> element)", () => {
  it("plans a fill for name/email even with no wrapping <form>", () => {
    buildAshbyForm();
    const plan = planStandardFieldFills(document, FULL_INFO, false, GENERIC_FIELD_DEFAULTS.standardFields);
    const bySelector = Object.fromEntries(plan.map((p) => [p.selector, p.value]));
    expect(bySelector["#_systemfield_name"]).toBe("Jane Doe");
    expect(bySelector["#_systemfield_email"]).toBe("jane@example.com");
  });

  it("skips the inferred #_systemfield_phone selector harmlessly when this org doesn't render one", () => {
    buildAshbyForm();
    const plan = planStandardFieldFills(document, FULL_INFO, false, GENERIC_FIELD_DEFAULTS.standardFields);
    expect(plan.some((p) => p.selector === "#_systemfield_phone")).toBe(false);
  });

  it("applyReactControlledFillPlan sets values and dispatches input/change", () => {
    buildAshbyForm();
    const nameInput = document.querySelector<HTMLInputElement>("#_systemfield_name")!;
    const events: string[] = [];
    nameInput.addEventListener("input", () => events.push("input"));
    nameInput.addEventListener("change", () => events.push("change"));

    const filled = applyReactControlledFillPlan(document, [{ selector: "#_systemfield_name", value: "Jane Doe" }]);

    expect(nameInput.value).toBe("Jane Doe");
    expect(events).toEqual(["input", "change"]);
    expect(filled).toEqual(["#_systemfield_name"]);
  });
});

describe("extractCustomQuestions", () => {
  it("extracts a genuine custom text question keyed by its data-field-path, with its real label", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    const phone = questions.find((q) => q.fieldName === "dd4dc7a2-c59a-463e-94e9-a27b546deb8b");
    expect(phone?.label).toBe("Phone number");
    expect(phone?.kind).toBe("text");
  });

  it("dedupes a radio group's multiple option inputs into one question, keyed by the group's own data-field-path (not the compound name/id)", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    const matching = questions.filter((q) => q.fieldName === "c2a1e903-aaaa-4b1a-9a1a-000000000099");
    expect(matching).toHaveLength(1);
    expect(matching[0]?.kind).toBe("radio");
    expect(matching[0]?.label).toBe("How did you hear about this role?");
  });

  it("D6 fix: excludes a demographic self-ID radio group entirely (not just kind:\"radio\"), matching Greenhouse's own EEOC block being fully hidden rather than merely human-only", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName === "d71f0f52-ff84-400e-97ed-e194e17ca8ca")).toBe(false);
  });

  it("never treats a _systemfield_ platform field as a custom question (by data-field-path prefix construction)", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName.startsWith("_systemfield_"))).toBe(false);
  });

  it("excludes any element with no data-field-path ancestor at all, e.g. the reCAPTCHA response and the autofill-helper file input", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName === "g-recaptcha-response")).toBe(false);
    expect(questions).toHaveLength(3); // phone + "how did you hear" + work-authorization (pronoun and gender-identity excluded by D6)
  });

  it("D6 fix: excludes a free-text demographic self-ID question by label text, the disclosed gap this file's own research flagged", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName === "8f3b1c4d-1111-4a1a-9a1a-000000000001")).toBe(false);
  });

  it("D6 fix: excludes the exact real disability-accommodation question that surfaced this gap, a euphemism with no word matching \"disab\"", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    expect(questions.some((q) => q.fieldName === "8f3b1c4d-3333-4a1a-9a1a-000000000003")).toBe(false);
  });

  it("D6 fix does NOT exclude a free-text work-authorization question -- a different category, left to the existing generate/verify/human-review pipeline", () => {
    buildAshbyForm();
    const questions = extractCustomQuestions(document);
    const workAuth = questions.find((q) => q.fieldName === "8f3b1c4d-2222-4a1a-9a1a-000000000002");
    expect(workAuth?.kind).toBe("text");
    expect(workAuth?.label).toBe("Please describe your current work authorization status");
  });
});

describe("fillCustomTextAnswer", () => {
  it("fills a genuine text question by its data-field-path name and dispatches events", () => {
    buildAshbyForm();
    const input = document.querySelector<HTMLInputElement>('[name="dd4dc7a2-c59a-463e-94e9-a27b546deb8b"]')!;
    const events: string[] = [];
    input.addEventListener("input", () => events.push("input"));
    input.addEventListener("change", () => events.push("change"));

    const filled = fillCustomTextAnswer(document, "dd4dc7a2-c59a-463e-94e9-a27b546deb8b", "+1-555-0100");

    expect(filled).toBe("filled");
    expect(input.value).toBe("+1-555-0100");
    expect(events).toEqual(["input", "change"]);
  });

  it("refuses a _systemfield_ name outright, without even querying the DOM for it", () => {
    buildAshbyForm();
    expect(fillCustomTextAnswer(document, "_systemfield_name", "x")).toBe("refused");
  });

  it("refuses a radio-group's own compound name -- E5 never generates a binary-choice answer", () => {
    buildAshbyForm();
    const filled = fillCustomTextAnswer(
      document,
      "bd7bdce0-e0ca-4dc3-bb51-277db2bcfeac_d71f0f52-ff84-400e-97ed-e194e17ca8ca",
      "He/him",
    );
    expect(filled).toBe("refused");
  });

  it("returns false for a field name that doesn't exist on the page", () => {
    buildAshbyForm();
    expect(fillCustomTextAnswer(document, "00000000-0000-0000-0000-000000000000", "x")).toBe("refused");
  });

  it("D6 fix: refuses to fill a free-text demographic self-ID question even if called directly, defense-in-depth on top of the extraction-side exclusion", () => {
    buildAshbyForm();
    const input = document.querySelector<HTMLInputElement>('[name="8f3b1c4d-1111-4a1a-9a1a-000000000001"]')!;
    const filled = fillCustomTextAnswer(document, "8f3b1c4d-1111-4a1a-9a1a-000000000001", "attacker-or-caller-supplied text");
    expect(filled).toBe("refused");
    expect(input.value).toBe("");
  });

  it("refuses a crafted field name that tries to break out of the attribute selector into an unrelated field", () => {
    buildAshbyForm();
    const email = document.querySelector<HTMLInputElement>("#_systemfield_email")!;
    email.value = "jane@example.com";

    const maliciousFieldName = 'x"], input[name="_systemfield_email';
    const filled = fillCustomTextAnswer(document, maliciousFieldName, "attacker-controlled text");

    expect(filled).toBe("refused");
    expect(email.value).toBe("jane@example.com");
  });
});

// ---------------------------------------------------------------------------
// E6 hardening. The original D6 pattern missed most realistic phrasings,
// was defeated by odd spellings of a tenant-controlled label, and treated
// a label it couldn't read as harmless. The full phrase table lives in
// questionSafety.test.ts; these check it is wired into Ashby's extract and
// fill paths, including the places the wording can hide.
// ---------------------------------------------------------------------------

// Built from char codes so this file never contains a raw invisible character.
const ZWSP = String.fromCharCode(0x200b);
const SOFT_HYPHEN = String.fromCharCode(0xad);

function entry(id: string, label: string | null, control: string, extra = ""): string {
  const labelHtml =
    label === null ? "" : `<label class="ashby-application-form-question-title" for="${id}">${label}</label>`;
  return `<div data-field-path="${id}" class="ashby-application-form-field-entry">${labelHtml}${extra}${control}</div>`;
}

function buildEntries(...entries: string[]): void {
  document.body.innerHTML = `<div class="ashby-application-form-container">${entries.join("\n")}</div>`;
}

const textControl = (id: string) => `<input type="text" id="${id}" name="${id}" />`;

describe("E6: D6 classifier is wired into extract and fill (Ashby)", () => {
  const SENSITIVE = [
    "What is your ethnic background?",
    "Ethnic origin",
    "Do you identify as LGBTQ+?",
    "What is your date of birth?",
    "Veterans",
    "Are you a veteran?",
    "Do you require reasonable adjustments?",
    "What is your religion or belief?",
    "What is your marital status?",
    "Are you pregnant?",
    "Are you neurodivergent?",
    `What is your gen${ZWSP}der?`,
    `What is your gen${SOFT_HYPHEN}der?`,
  ];

  for (const label of SENSITIVE) {
    it(`excludes ${JSON.stringify(label)} from the list and refuses to fill it`, () => {
      buildEntries(entry("aaaa-1", label, textControl("aaaa-1")));
      expect(extractCustomQuestions(document).some((x) => x.fieldName === "aaaa-1")).toBe(false);
      expect(fillCustomTextAnswer(document, "aaaa-1", "any text")).toBe("refused");
      expect(document.querySelector<HTMLInputElement>("#aaaa-1")!.value).toBe("");
    });
  }
});

describe("E6: the self-ID wording doesn't have to be in the title (Ashby)", () => {
  it("excludes an entry whose DESCRIPTION carries the self-identification wording", () => {
    buildEntries(
      entry(
        "bbbb-1",
        "Optional",
        textControl("bbbb-1"),
        `<div class="ashby-application-form-question-description">Voluntary self-identification: how do you describe your ethnic background?</div>`,
      ),
    );
    expect(extractCustomQuestions(document).some((x) => x.fieldName === "bbbb-1")).toBe(false);
    expect(fillCustomTextAnswer(document, "bbbb-1", "text")).toBe("refused");
  });

  it("excludes an entry that sits under a self-identification SECTION HEADING", () => {
    document.body.innerHTML = `
      <div class="ashby-application-form-section-container">
        <h2>Voluntary Self Identification</h2>
        ${entry("cccc-1", "Tell us more", textControl("cccc-1"))}
      </div>
      <div class="ashby-application-form-section-container">
        <h2>Your application</h2>
        ${entry("cccc-2", "Tell us more", textControl("cccc-2"))}
      </div>`;
    const listed = extractCustomQuestions(document).map((x) => x.fieldName);
    expect(listed).not.toContain("cccc-1");
    expect(listed).toContain("cccc-2");
    expect(fillCustomTextAnswer(document, "cccc-1", "text")).toBe("refused");
    expect(fillCustomTextAnswer(document, "cccc-2", "text")).toBe("filled");
  });
});

describe("E6: an unreadable label fails closed (Ashby)", () => {
  it("does not list a text field as draftable when the platform's title class is absent (e.g. renamed)", () => {
    // The wording IS sensitive, but under a class this extension doesn't
    // read -- it must not fail open as an ordinary draftable question.
    buildEntries(
      `<div data-field-path="dddd-1" class="ashby-application-form-field-entry">
         <label class="some-renamed-class" for="dddd-1">What is your gender?</label>
         ${textControl("dddd-1")}
       </div>`,
    );
    const found = extractCustomQuestions(document).find((x) => x.fieldName === "dddd-1");
    expect(found?.label).toBeNull();
    expect(found?.kind).toBe("radio");
    expect(fillCustomTextAnswer(document, "dddd-1", "text")).toBe("refused");
  });
});

describe("E6: control classification fails closed (Ashby)", () => {
  it.each(["date", "number"])("lists an <input type=%s> as human-only", (type) => {
    buildEntries(entry("eeee-1", "Earliest start date", `<input type="${type}" id="eeee-1" name="eeee-1" />`));
    expect(extractCustomQuestions(document).find((x) => x.fieldName === "eeee-1")?.kind).toBe("radio");
    expect(fillCustomTextAnswer(document, "eeee-1", "2026-01-01")).toBe("refused");
  });

  it("does NOT exclude a free-text work-authorization question -- a maintainer decision, pinned here", () => {
    buildEntries(entry("ffff-1", "Do you require visa sponsorship?", textControl("ffff-1")));
    expect(extractCustomQuestions(document).find((x) => x.fieldName === "ffff-1")?.kind).toBe("text");
    expect(fillCustomTextAnswer(document, "ffff-1", "No")).toBe("filled");
  });
});

describe("E6: D5 -- a per-question fill never overwrites text already on the page (Ashby)", () => {
  it("returns 'not_empty' and leaves a hand-typed answer untouched", () => {
    buildEntries(entry("gggg-1", "Why us?", `<textarea id="gggg-1" name="gggg-1"></textarea>`));
    const area = document.querySelector<HTMLTextAreaElement>("#gggg-1")!;
    area.value = "my own hand-written answer";
    const events: string[] = [];
    area.addEventListener("input", () => events.push("input"));

    expect(fillCustomTextAnswer(document, "gggg-1", "LLM draft")).toBe("not_empty");
    expect(area.value).toBe("my own hand-written answer");
    expect(events).toEqual([]);
  });

  it("treats whitespace-only content as empty", () => {
    buildEntries(entry("gggg-2", "Why us?", textControl("gggg-2")));
    document.querySelector<HTMLInputElement>("#gggg-2")!.value = "  ";
    expect(fillCustomTextAnswer(document, "gggg-2", "LLM draft")).toBe("filled");
  });
});

// ---------------------------------------------------------------------------
// E6 continuation -- the per-question "Replace" action's `force` parameter.
// Mirrors lever.test.ts's/greenhouse.test.ts's own force suite: proves
// force bypasses ONLY D5, never the D6 sensitive-label/context refusals or
// the systemfield/entry-container refusals.
// ---------------------------------------------------------------------------

describe("E6 continuation: fillCustomTextAnswer's force parameter (Ashby)", () => {
  it("force:true overwrites existing text", () => {
    buildEntries(entry("gggg-1", "Why us?", `<textarea id="gggg-1" name="gggg-1"></textarea>`));
    const area = document.querySelector<HTMLTextAreaElement>("#gggg-1")!;
    area.value = "my own hand-written answer";

    expect(fillCustomTextAnswer(document, "gggg-1", "Replacement draft", true)).toBe("filled");
    expect(area.value).toBe("Replacement draft");
  });

  it("force:false (the default) still never overwrites -- the existing D5 behavior is unchanged", () => {
    buildEntries(entry("gggg-1", "Why us?", `<textarea id="gggg-1" name="gggg-1"></textarea>`));
    const area = document.querySelector<HTMLTextAreaElement>("#gggg-1")!;
    area.value = "my own hand-written answer";

    expect(fillCustomTextAnswer(document, "gggg-1", "LLM draft", false)).toBe("not_empty");
    expect(fillCustomTextAnswer(document, "gggg-1", "LLM draft")).toBe("not_empty");
    expect(area.value).toBe("my own hand-written answer");
  });

  it("force cannot make a D6-sensitive field fillable", () => {
    buildEntries(entry("aaaa-1", "What is your gender identity?", textControl("aaaa-1")));
    const input = document.querySelector<HTMLInputElement>("#aaaa-1")!;
    input.value = "already has text";

    expect(fillCustomTextAnswer(document, "aaaa-1", "any text", true)).toBe("refused");
    expect(input.value).toBe("already has text");
  });

  it("force cannot make a _systemfield_ or unmatched field fillable", () => {
    buildAshbyForm();
    expect(fillCustomTextAnswer(document, "_systemfield_name", "x", true)).toBe("refused");
    expect(fillCustomTextAnswer(document, "00000000-0000-0000-0000-000000000000", "x", true)).toBe("refused");
  });
});
