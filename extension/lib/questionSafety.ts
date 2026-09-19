// Shared guards for the custom-question path (extraction, LLM drafting,
// fill) on all three ATSs. Until E6 the D6 label check existed only in
// lib/ashby.ts, and Lever/Greenhouse relied purely on their field
// namespaces -- but the namespace is chosen by the TENANT: anyone can
// create a Lever, Greenhouse or Ashby account, so an org can author a
// demographic or accommodation question as an ordinary custom question,
// and every string on the page (labels included) is attacker-controllable.
// So the rules below run on every ATS, at extraction time and again at
// fill time, and they fail closed: anything not affirmatively a plain text
// control with a readable, non-sensitive label stays human-only.

/** `radio` is the human-only bucket: choices, dates, numbers, custom
 * widgets, and anything this extension can't positively identify as a
 * plain text control. The panel never offers to draft for it. */
export type QuestionKind = "text" | "file" | "radio";

/** What a per-question fill did. `not_empty` is D5: the target already
 * holds text (typed by the user, or filled earlier), so it was left alone. */
export type FillAnswerOutcome = "filled" | "not_empty" | "refused";

// ---- Reading labels ------------------------------------------------------

// Long enough for any real question, short enough that a hostile tenant
// can't flood the panel, the draft request, or the memory key with it.
const MAX_LABEL_LENGTH = 300;

/** Whitespace-collapsed text with control and format characters (zero-width
 * spaces, soft hyphens, bidi overrides, BEL...) removed. Whitespace is
 * collapsed first so newlines/tabs become spaces rather than being deleted
 * along with the other control characters. */
export function cleanText(raw: string | null | undefined): string {
  if (raw === null || raw === undefined) return "";
  return raw
    .replace(/\s+/g, " ")
    .replace(/[\p{Cc}\p{Cf}]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

function capLabel(clean: string): string | null {
  if (clean === "") return null;
  if (clean.length <= MAX_LABEL_LENGTH) return clean;
  // Slice by UTF-16 units first so a multi-megabyte label never gets
  // spread into an array, then trim to whole code points.
  const head = [...clean.slice(0, MAX_LABEL_LENGTH)].slice(0, MAX_LABEL_LENGTH - 1);
  return `${head.join("").trimEnd()}…`;
}

/**
 * The label to display/send/remember for a question, plus whether the
 * FULL, untruncated label is D6-sensitive. Classification deliberately runs
 * before the length cap: capping first would let a tenant pad a label so
 * the giveaway word falls off the end.
 */
export function readQuestionLabel(raw: string | null | undefined): {
  label: string | null;
  sensitive: boolean;
} {
  const clean = cleanText(raw);
  return { label: capLabel(clean), sensitive: isSensitiveSelfIdText(clean) };
}

// ---- D6: voluntary self-identification and accommodation -----------------
//
// D6: EEO/demographic/disability-accommodation fields are opt-in only --
// never auto-filled, never sent to the LLM. Scope is genuine voluntary
// self-ID data (gender, sex, sexual orientation, race/ethnicity, disability
// and accommodation, veteran/military status, religion, age/birth date,
// marital/family status, national origin, pregnancy, caste, first-
// generation/underrepresented status, and the section titles that
// introduce them). Work-authorization / visa / sponsorship questions are
// deliberately NOT in scope -- that is a maintainer decision (see the note
// in lib/ashby.ts's history and work-authorization-status.md), and the
// tests pin it.
//
// Deliberately over-inclusive: a false positive costs the user one field
// answered directly on the page instead of through the panel; a false
// negative puts a real self-ID answer into an LLM prompt, which is the
// harm D6 exists to prevent. Known cheap false positives ("race
// condition", "feature flag was disabled") are accepted on that basis.
//
// Matching runs on normalized text (NFKD, combining marks and invisible
// characters stripped, lowercased) because the label is tenant-controlled:
// "gender" split by a zero-width space, spelled in fullwidth letters,
// accented, or with a Cyrillic "e" swapped in must not slip past. Every
// pattern is a plain prefix/word match -- no leading
// wildcards or nested quantifiers -- so a multi-megabyte label can't make
// the scan superlinear.

const LATIN_TERMS: string[] = [
  // gender, sex, sexual orientation
  "gender", // also cisgender, transgender, agender, genderqueer
  "genero",
  "geschlecht",
  "identita di genere",
  String.raw`\bsex`, // sex, sexual, sexuality, sexuelle
  "sexual", // bisexual, homosexual, heterosexual
  String.raw`\bsesso\b`,
  String.raw`\bsessual`,
  String.raw`\bpronoun`,
  String.raw`\b(?:he|she|they)\s*/\s*(?:him|her|them)\b`,
  String.raw`\bwom[ae]n\b`,
  String.raw`\bwomxn\b`,
  String.raw`\bfemale\b`,
  String.raw`\bmale\b`,
  String.raw`\bnon[- ]?binary\b`,
  String.raw`\btrans\b`,
  String.raw`\btwo[- ]spirit`,
  String.raw`\blgbt`,
  String.raw`\bqueer\b`,
  String.raw`\bgay\b`,
  String.raw`\blesbian\b`,
  String.raw`\bidentif(?:y|ies|ied) as\b`,
  String.raw`\bhow do you identify\b`,
  // race, ethnicity, national origin, caste
  String.raw`\brace\b`,
  String.raw`\braces\b`,
  "racial", // multiracial, biracial, racially
  String.raw`\bare you (?:an? )?(?:white|black|asian)\b`,
  String.raw`\bethnic`, // ethnic, ethnicity
  String.raw`\bethniq`,
  String.raw`\betnic`,
  String.raw`\bethnie\b`,
  String.raw`\bethnisch`,
  String.raw`\braza\b`,
  String.raw`\brasse\b`,
  String.raw`\braca\b`,
  String.raw`\bhispanic\b`,
  String.raw`\blatin`, // latino/a/x/e, latin american
  String.raw`\basian\b`,
  String.raw`\bcaucasian\b`,
  String.raw`\bafrican[- ]american\b`,
  String.raw`\bnative (?:hawaiian|american|alaskan?)\b`,
  String.raw`\balaska native\b`,
  String.raw`\bpacific islander\b`,
  String.raw`\baapi\b`,
  String.raw`\bpersons? of colou?r\b`,
  String.raw`\bbipoc\b`,
  String.raw`\bindigenous\b`,
  String.raw`\btribal\b`,
  String.raw`\bminorit`,
  String.raw`\bunderrepresented\b`,
  String.raw`\bfirst[- ]generation\b`,
  String.raw`\bcaste\b`,
  String.raw`\bsocial category\b`,
  String.raw`\bnationalit`,
  String.raw`\bnational origin\b`,
  String.raw`\bnacionalidad\b`,
  String.raw`\bstaatsangehorigkeit\b`,
  String.raw`\bnazionalita\b`,
  // religion, age, birth, family
  String.raw`\breligio`,
  String.raw`\bfaith\b`,
  String.raw`\bage\b`,
  String.raw`\bhow old\b`,
  String.raw`\bbirth`,
  String.raw`\bdob\b`,
  String.raw`\bmarital\b`,
  String.raw`\bmarried\b`,
  String.raw`\bspouse`,
  String.raw`\bdependents?\b`,
  String.raw`\bpregnan`,
  String.raw`\bestado civil\b`,
  String.raw`\bfamilienstand\b`,
  String.raw`\bstato civile\b`,
  // disability, health, accommodation
  String.raw`\bdisab`,
  String.raw`\bdiscapacid`,
  String.raw`\bdiscapacit`,
  String.raw`\bbehinderung`,
  String.raw`\bschwerbehinder`,
  String.raw`\bimpair`,
  String.raw`\bhandicap`,
  String.raw`\bhealth (?:condition|issue|problem)s?\b`,
  String.raw`\bchronic (?:illness|condition|disease|pain)`,
  String.raw`\bmedical (?:condition|history|issue|need)s?\b`,
  String.raw`\bmental health\b`,
  String.raw`\bneurodiver`, // neurodiverse, neurodivergent, neurodiversity
  String.raw`\bneurotypical\b`,
  String.raw`\bdeaf\b`,
  String.raw`\bhard of hearing\b`,
  String.raw`\bdyslex`,
  String.raw`\bwheelchair\b`,
  String.raw`\bautis`,
  String.raw`\badhd\b`,
  String.raw`\baccomm?odat`, // accommodate/accommodation, and the common "accomodation"
  String.raw`\breasonable adjust`,
  String.raw`\bspecial assistance\b`,
  String.raw`\baccess (?:requirement|need)s?\b`,
  String.raw`\bsupport needs?\b`,
  // veteran and military status
  String.raw`\bveterans?\b`,
  String.raw`\bveterano`,
  String.raw`\bmilitary (?:status|service|spouse|veteran|branch|affiliation|background|history)\b`,
  String.raw`\bex-?military\b`,
  String.raw`\bformer military\b`,
  String.raw`\barmed forces\b`,
  String.raw`\breservist`,
  String.raw`\bservice ?members?\b`,
  String.raw`\bnational guard\b`,
  // the section/self-ID titles that introduce all of the above
  String.raw`\bself[- ]?id`, // self id, self-identify, self identification
  String.raw`\beeo`,
  String.raw`\bequal (?:employment )?opportunit`,
  String.raw`\bdiversity (?:survey|monitoring|questionnaire|information|data|form|section)\b`,
  String.raw`\bdemographic`,
  String.raw`\baffirmative action\b`,
  String.raw`\bofccp\b`,
  String.raw`\bprotected (?:class|classes|categor|characteristic|group)`,
];

// Scripts where ASCII \b means nothing: CJK has no word spaces, and Cyrillic
// "пол" (sex) needs a real letter boundary so it doesn't match inside longer
// words. Matched against normalized text WITHOUT the confusable fold below,
// which would otherwise turn genuine Cyrillic into a mix of scripts.
const NON_LATIN_TERMS: string[] = [
  "性别",
  "性別",
  "残疾",
  "殘疾",
  "种族",
  "種族",
  "民族",
  "宗教",
  "年龄",
  "年齡",
  "国籍",
  "國籍",
  String.raw`(?<![\p{L}\p{N}])пол(?![\p{L}\p{N}])`,
  "гендер",
  "инвалид",
  "этнич",
  "национальност",
  "религи",
  "ветеран",
  "جنس", // also matches الجنس
  "اعاقة", // إعاقة once its hamza is stripped by normalization
];

const LATIN_PATTERN = new RegExp(LATIN_TERMS.join("|"), "u");
const NON_LATIN_PATTERN = new RegExp(NON_LATIN_TERMS.join("|"), "u");

// Letters from other scripts that render identically to Latin ones. A
// tenant who wants to slip a label past the check swaps one in; folding
// them back makes the disguised word match. Only exact lookalikes -- this
// is not a general transliteration. Written as escapes on purpose: a source
// file full of look-alike letters is exactly what a reviewer can't audit.
const CONFUSABLES: Record<string, string> = {
  "\u0430": "a", // Cyrillic a
  "\u0441": "c", // Cyrillic es
  "\u0435": "e", // Cyrillic ie
  "\u043e": "o", // Cyrillic o
  "\u0440": "p", // Cyrillic er
  "\u0445": "x", // Cyrillic ha
  "\u0443": "y", // Cyrillic u
  "\u0456": "i", // Cyrillic byelorussian-ukrainian i
  "\u0458": "j", // Cyrillic je
  "\u0455": "s", // Cyrillic dze
  "\u04bb": "h", // Cyrillic shha
  "\u0501": "d", // Cyrillic komi de
  "\u051b": "q", // Cyrillic qa
  "\u051d": "w", // Cyrillic we
  "\u0261": "g", // Latin script g
  "\u0131": "i", // Latin dotless i
  "\u03bf": "o", // Greek omicron
  "\u03bd": "v", // Greek nu
  "\u03c1": "p", // Greek rho
  "\u03b1": "a", // Greek alpha
  "\u03b5": "e", // Greek epsilon
  "\u03b9": "i", // Greek iota
  "\u03ba": "k", // Greek kappa
  "\u03c4": "t", // Greek tau
  "\u03c5": "u", // Greek upsilon
  "\u03c7": "x", // Greek chi
};
const CONFUSABLE_PATTERN = new RegExp(`[${Object.keys(CONFUSABLES).join("")}]`, "gu");

function foldConfusables(text: string): string {
  return text.replace(CONFUSABLE_PATTERN, (ch) => CONFUSABLES[ch] ?? ch);
}

function normalizeForMatching(clean: string): string {
  return clean
    .normalize("NFKD")
    .replace(/\p{M}+/gu, "")
    .replace(/[\p{Cc}\p{Cf}]/gu, "")
    .toLowerCase();
}

/** True when `text` reads like a voluntary self-identification or
 * accommodation question (D6). `null`/empty is false here -- callers decide
 * separately what an unreadable label means (they treat it as human-only). */
export function isSensitiveSelfIdText(text: string | null | undefined): boolean {
  const normalized = normalizeForMatching(cleanText(text));
  if (normalized === "") return false;
  return LATIN_PATTERN.test(foldConfusables(normalized)) || NON_LATIN_PATTERN.test(normalized);
}

// ---- Control classification ----------------------------------------------

const TEXT_INPUT_TYPES = new Set(["text", "email", "tel", "url"]);

/** A plain text-entry element: a textarea, or an input of a text-like type.
 * The single definition every write path uses, so what extraction lists,
 * what standard-field fill will touch, and what a per-question fill will
 * write can never drift apart. */
export function isTextEntryElement(
  element: Element,
): element is HTMLInputElement | HTMLTextAreaElement {
  if (element.tagName === "TEXTAREA") return true;
  return element.tagName === "INPUT" && TEXT_INPUT_TYPES.has((element as HTMLInputElement).type);
}

/**
 * Fail-closed classification: `text` only for a plain text-entry control;
 * `file` for a file input; everything else -- select, radio, checkbox,
 * combobox, date, number, div/fieldset wrappers, custom widgets, unknown
 * tags -- is `radio`, the human-only bucket. Defaulting the other way (as
 * Lever and Greenhouse used to) let a native <select> work-authorization
 * dropdown be listed as free text and offered for LLM drafting.
 */
export function controlKind(element: Element): QuestionKind {
  if (element.getAttribute("role") === "combobox") return "radio";
  if (element.tagName === "INPUT" && (element as HTMLInputElement).type === "file") return "file";
  return isTextEntryElement(element) ? "text" : "radio";
}

/** `controlKind`, plus: a text field whose label couldn't be read is also
 * human-only. Its only possible "question text" would be a raw field name
 * or UUID, and nothing verifies it isn't a sensitive question under a
 * renamed label class. */
export function questionKind(element: Element, label: string | null): QuestionKind {
  const kind = controlKind(element);
  return kind === "text" && label === null ? "radio" : kind;
}

/** D5: text already in a field is the user's (typed by hand, or filled by an
 * earlier pass) -- a per-question fill leaves it alone. */
export function hasExistingText(element: HTMLInputElement | HTMLTextAreaElement): boolean {
  return element.value.trim() !== "";
}

/**
 * A model-drafted (or remembered) answer as the human will review it.
 * Control and format characters (bidi overrides, zero-width text) are
 * dropped and runs of blank lines collapsed: the review box only shows a
 * few lines at a time, so a draft padded with blank lines could park extra
 * content below the fold and have it filled into the tenant's form
 * unseen. Newlines and tabs are kept -- they're real formatting.
 */
export function sanitizeDraftText(raw: string): string {
  return raw
    .replace(/\r\n?/g, "\n")
    .replace(/[\p{Cc}\p{Cf}]/gu, (ch) => (ch === "\n" || ch === "\t" ? ch : ""))
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
