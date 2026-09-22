import { describe, expect, it } from "vitest";
import {
  cleanText,
  controlKind,
  hasExistingText,
  isSensitiveSelfIdText,
  isTextEntryElement,
  questionKind,
  readQuestionLabel,
  sanitizeDraftText,
} from "@/lib/questionSafety";

// E6 -- the shared D6 classifier. Before this, only Ashby had a label
// check at all, and its regex missed most realistic phrasings. Every
// phrase below is verbatim from the adversarial reviewers' repros (the
// ones the old pattern let through as draftable free text), so this table
// is the regression suite for that finding, not a paraphrase of it.

const SHOULD_BE_EXCLUDED: Record<string, string[]> = {
  "race and ethnicity": [
    "What is your ethnic background?",
    "Ethnic origin",
    "Origen étnico",
    "Are you Asian?",
    "Are you white?",
    "Black or African American",
    "Caucasian",
    "Native Hawaiian",
    "Alaska Native",
    "AAPI",
    "Person of color",
    "BIPOC",
    "Multiracial",
    "Biracial",
    "Racially",
    "Do you identify as a person of color / BIPOC?",
  ],
  "Latinx / Latine": ["Do you identify as Latinx?", "Are you Latine?", "Latin American descent"],
  "gender and sex without the word gender": [
    "Are you a woman?",
    "female",
    "Are you male or female?",
    "Male/Female",
    "non-binary",
    "Nonbinary",
    "Are you trans?",
    "Cisgender",
    "Agender",
    "Genderqueer",
    "Womxn",
    "Two-spirit",
    "He/him, she/her, they/them",
    "Genders",
    "Do you identify as a woman?",
    "What are your preferred pronouns?",
    "Preferred pronouns",
    "Pronouns",
    "Gender",
    "What is your gender identity?",
    "gender expression",
  ],
  "orientation and LGBTQ": [
    "LGBTQ+",
    "LGBTQIA+",
    "LGBT community",
    "Sexuality",
    "Sexual preference",
    "Gay, lesbian, bisexual, straight",
    "Do you identify as LGBTQ+?",
    "Which sexual identity best describes you?",
  ],
  "disability and health": [
    "impairment",
    "Handicap",
    "health condition",
    "chronic illness",
    "medical conditions",
    "Mental health",
    "neurodivergent",
    "neurodiverse",
    "Neurodiversity",
    "Deaf or hard of hearing",
    "Dyslexia",
    "Wheelchair access needs",
    "Do you have a disability? Please describe.",
    "Do you have a health condition or impairment?",
    "Are you neurodivergent?",
  ],
  "accommodation": [
    "reasonable adjustments",
    "special assistance during interviews",
    "access requirements",
    "support needs",
    "accomodation",
    "Do you require any accommodations or support during the interview(s)?",
    "Do you require accommodations?",
  ],
  "veteran and military": [
    "Veterans",
    "Military status",
    "armed forces",
    "Reservist",
    "military spouse",
    "Recently separated service member",
    "Ex-military",
    "Are you a veteran?",
    "What is your veteran status?",
  ],
  "self-ID section titles": [
    "Voluntary Self Identification",
    "Self identification",
    "Self ID",
    "EEO",
    "Equal Employment Opportunity information",
    "Diversity survey",
    "Diversity monitoring",
    "Demographics",
    "Affirmative action",
    "OFCCP",
    "Protected class",
  ],
  "other protected characteristics": [
    "Religion",
    "religious affiliation",
    "Faith",
    "What is your religion or belief?",
    "national origin",
    "What is your nationality?",
    "marital status",
    "What is your marital status?",
    "Are you married?",
    "Spouse's name",
    "number of dependents",
    "Caste / Category (General, OBC, SC, ST)",
    "What is your caste?",
    "Social category",
    "Date of birth",
    "What is your date of birth?",
    "DOB",
    "Birthdate",
    "What is your age?",
    "What is your age range?",
    "How old are you?",
    "Pregnancy",
    "Are you pregnant or nursing?",
    "Are you pregnant?",
    // E6 -- the three confirmed-missing phrasings this pass closed.
    "Family status",
    "Marital or family status",
    "Do you have children?",
    "Do you have any children?",
    "Number of children",
    "Do you have kids?",
    "Country of origin",
    "What is your country of origin?",
    // d6-2 -- real-world word-order/statutory-term variants the original
    // three E6 phrasings above missed (see questionSafety.ts's own d6-2
    // comments).
    "What is your familial status?",
    "Familial status (protected class)",
    "Parental status",
    "Origin Country",
    "Country/Region of Origin",
    "first-generation student",
    "Are you a first-generation college student?",
    "Underrepresented minority",
    "Are you a member of an underrepresented group?",
    "Indigenous/tribal",
  ],
  "non-English": [
    "Género",
    "Geschlecht",
    "Sexo",
    "Discapacidad",
    "Behinderung",
    "Identità di genere",
    "Origine ethnique",
    "性别",
    "残疾",
    "Пол",
    "الجنس",
  ],
};

// The maintainer's boundary: work-authorization / visa / sponsorship
// questions are NOT D6 self-ID and must stay visible and draftable (they
// flow through the existing generate/verify/human-review pipeline). The
// rest are ordinary questions the classifier must not swallow.
const MUST_STAY_VISIBLE = [
  "Are you legally authorized to work in the US?",
  "Will you now or in the future require visa sponsorship for employment?",
  "Do you require sponsorship?",
  "What is your work authorization status?",
  "Please describe your current work authorization status",
  "Are you a US citizen or permanent resident?",
  "Are you eligible to work in the US?*",
  "Why do you want to work here?",
  "What is your current notice period?",
  "How did you hear about this role?",
  "What are your salary expectations?",
  "Are you willing to relocate?",
  "Please describe your experience with Python.",
  "LinkedIn Profile",
  "GitHub URL",
  "Portfolio",
  "Cover Letter",
  "Did someone from The Athletic refer you? *",
  "Phone number",
  // E6 -- the false-positive check for the three new terms added this
  // pass: real, plausible logistics questions that share a word with the
  // new patterns ("family", "country", "kid") but aren't self-ID, and
  // must not be swept in by a too-broad pattern.
  "What is your current notice period?",
  "What is your available start date?",
  "Country of residence",
  "What country do you currently reside in?",
  "Did a family member refer you to this role?",
  "Are you eligible for our family medical leave policy?",
  "Kid-friendly office tour available on request",
];

describe("isSensitiveSelfIdText -- D6 vocabulary", () => {
  for (const [group, phrases] of Object.entries(SHOULD_BE_EXCLUDED)) {
    describe(group, () => {
      for (const phrase of phrases) {
        it(`excludes ${JSON.stringify(phrase)}`, () => {
          expect(isSensitiveSelfIdText(phrase)).toBe(true);
        });
      }
    });
  }

  describe("does NOT exclude work-authorization or ordinary questions", () => {
    for (const phrase of MUST_STAY_VISIBLE) {
      it(`leaves ${JSON.stringify(phrase)} alone`, () => {
        expect(isSensitiveSelfIdText(phrase)).toBe(false);
      });
    }
  });

  it("treats null, undefined and empty text as not sensitive (callers decide what an unreadable label means)", () => {
    expect(isSensitiveSelfIdText(null)).toBe(false);
    expect(isSensitiveSelfIdText(undefined)).toBe(false);
    expect(isSensitiveSelfIdText("   ")).toBe(false);
  });
});

describe("isSensitiveSelfIdText -- tenant-controlled spellings", () => {
  // The label is attacker-controllable text, so an honest-looking question
  // must not be defeatable by the spelling of one word.
  const disguises: Record<string, string> = {
    "zero-width space inside the word": "What is your gen\u200Bder?",
    "zero-width space at the start": "\u200Bgender",
    "soft hyphen inside the word": "What is your gen\u00ADder?",
    "zero-width joiner and word joiner": "What is your g\u200Den\u2060der?",
    "fullwidth letters": "What is your \uFF47\uFF45\uFF4E\uFF44\uFF45\uFF52?",
    "an accented letter": "What is your G\u00EBnder?",
    "a combining mark": "What is your gende\u0308r?",
    "a Cyrillic letter standing in for a Latin one": "What is your g\u0435nder?",
    "a Greek letter standing in for a Latin one": "Are you a v\u03B5teran?",
    "a right-to-left override wrapped around it": "\u202Eredneg\u202C gender",
    "mixed case": "wHaT iS yOuR gEnDeR?",
    "a soft hyphen in a multi-word phrase": "sexual or\u00ADientation",
  };
  for (const [name, label] of Object.entries(disguises)) {
    it(`still excludes it with ${name}`, () => {
      expect(isSensitiveSelfIdText(label)).toBe(true);
    });
  }

  it("does not treat genuine Cyrillic text as a disguise for something else", () => {
    expect(isSensitiveSelfIdText("Какова ваша зарплата?")).toBe(false);
  });
});

describe("cleanText / readQuestionLabel", () => {
  it("collapses whitespace and strips control, zero-width and bidi characters", () => {
    expect(cleanText("  Why\u0007 us?\u200B \u202E gnorw \u2066 Verified\u200B\n\t ")).toBe("Why us? gnorw Verified");
  });

  it("turns newlines and tabs into spaces rather than gluing words together", () => {
    expect(cleanText("first line\nsecond\tline")).toBe("first line second line");
  });

  it("returns null for an empty or whitespace-only label", () => {
    expect(readQuestionLabel("  \u200B  ").label).toBeNull();
    expect(readQuestionLabel(null).label).toBeNull();
    expect(readQuestionLabel(undefined).label).toBeNull();
  });

  it("caps a hostile, oversized label", () => {
    const { label } = readQuestionLabel("x".repeat(5_000));
    expect(label).not.toBeNull();
    expect(label!.length).toBeLessThanOrEqual(300);
    expect(label!.endsWith("…")).toBe(true);
  });

  it("does not split a surrogate pair when it truncates", () => {
    const { label } = readQuestionLabel("\u{1F600}".repeat(400));
    expect(label).not.toBeNull();
    expect(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(label!)).toBe(false);
    expect(/(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(label!)).toBe(false);
  });

  it("classifies the FULL label, not the truncated one -- padding can't push the giveaway word past the cap", () => {
    const padded = `${"Please tell us more about yourself. ".repeat(20)}What is your gender?`;
    const { label, sensitive } = readQuestionLabel(padded);
    expect(label!.length).toBeLessThanOrEqual(300);
    expect(label).not.toMatch(/gender/i);
    expect(sensitive).toBe(true);
  });

  it("scans a multi-megabyte label in linear time (no catastrophic backtracking)", () => {
    const started = performance.now();
    isSensitiveSelfIdText("a".repeat(5_000_000));
    isSensitiveSelfIdText("a ".repeat(2_500_000));
    expect(performance.now() - started).toBeLessThan(3_000);
  });
});

describe("controlKind / questionKind -- fail closed", () => {
  function el(html: string): Element {
    document.body.innerHTML = html;
    return document.body.firstElementChild!;
  }

  it.each([
    ['<textarea></textarea>', "text"],
    ['<input type="text" />', "text"],
    ['<input />', "text"],
    ['<input type="email" />', "text"],
    ['<input type="tel" />', "text"],
    ['<input type="url" />', "text"],
    ['<input type="file" />', "file"],
    ['<select><option>Yes</option></select>', "radio"],
    ['<select multiple></select>', "radio"],
    ['<input type="radio" />', "radio"],
    ['<input type="checkbox" />', "radio"],
    ['<input type="date" />', "radio"],
    ['<input type="number" />', "radio"],
    ['<input type="password" />', "radio"],
    ['<input type="text" role="combobox" />', "radio"],
    ['<div></div>', "radio"],
    ['<fieldset></fieldset>', "radio"],
    ['<div contenteditable="true"></div>', "radio"],
    ['<button type="submit"></button>', "radio"],
  ])("%s -> %s", (html, expected) => {
    expect(controlKind(el(html))).toBe(expected);
  });

  it("puts a text field whose label couldn't be read in the human-only bucket", () => {
    const input = el('<input type="text" />');
    expect(questionKind(input, "Why us?")).toBe("text");
    expect(questionKind(input, null)).toBe("radio");
  });

  it("isTextEntryElement is the same allow-list, without the combobox/label rules", () => {
    expect(isTextEntryElement(el('<input type="text" role="combobox" />'))).toBe(true);
    expect(isTextEntryElement(el("<select></select>"))).toBe(false);
  });
});

describe("hasExistingText", () => {
  it("is true only for non-blank content", () => {
    const input = document.createElement("input");
    expect(hasExistingText(input)).toBe(false);
    input.value = "   ";
    expect(hasExistingText(input)).toBe(false);
    input.value = "typed by hand";
    expect(hasExistingText(input)).toBe(true);
  });
});

describe("sanitizeDraftText", () => {
  it("collapses runs of blank lines so a draft can't park content below the fold", () => {
    expect(sanitizeDraftText("First paragraph.\n\n\n\n\n\n\nHidden tail.")).toBe("First paragraph.\n\nHidden tail.");
  });

  it("drops control and format characters but keeps newlines and tabs", () => {
    expect(sanitizeDraftText("Line one\u202E\u200B\u0007\nLine\ttwo")).toBe("Line one\nLine\ttwo");
  });

  it("normalizes CRLF and trims", () => {
    expect(sanitizeDraftText("  a\r\nb\r\n\r\n\r\nc  ")).toBe("a\nb\n\nc");
  });
});
