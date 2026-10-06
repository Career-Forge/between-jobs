import type { StandardFieldSpec } from "./ats-field-map";
import {
  hasExistingText,
  isSensitiveSelfIdText,
  isTextEntryElement,
  questionKind,
  readQuestionLabel,
  type FillAnswerOutcome,
  type QuestionKind,
} from "./questionSafety";
import { setReactControlledValue, type FieldFillPlanItem } from "./standardFields";

// E5 (browser-extension.md) -- "Zero open-source prior art exists
// anywhere for this ATS -- cold start," confirmed: no reference
// implementation was found or mined for Ashby specifically. Everything
// below comes from live inspection of two real, unrelated postings
// (jobs.ashbyhq.com/foundry-for-good, jobs.ashbyhq.com/everai) on
// 2026-09-13, cross-checked against a third (jobs.ashbyhq.com/ema) for
// the systemfield/phone-instability question specifically.
//
// Real, load-bearing findings from that research:
//
// - Ashby renders its apply form with NO `<form>` element at all
//   (confirmed: `document.querySelector("form")` returns null on a real
//   /application page even once the form is fully hydrated) -- it
//   submits via its own JS/fetch, not a native form POST. Every
//   selector here is scoped to `document`, never to a `<form>`.
// - The apply page itself lives at `<posting-url>/application`, not the
//   posting's own base URL (which only shows the job description plus
//   an "Apply for this Job" link) -- detection must work on the
//   `/application` path specifically.
// - Ashby is confirmed React-controlled, same as Greenhouse: a plain
//   `.value =` assignment left the field's own `__reactProps$...` at the
//   old value while the DOM's `.value` visibly changed; the same
//   `setReactControlledValue` native-setter workaround
//   (lib/standardFields.ts) was confirmed live to fix it.
// - Ashby's own "systemfield" convention (`_systemfield_name`,
//   `_systemfield_email`, `_systemfield_resume`) is confirmed stable
//   across all three companies tested -- this is Ashby's platform-level
//   equivalent of Greenhouse's `first_name`/`email`/etc ids, safe to
//   treat as a generic, open-source baseline.
// - Confirmed phone instability, exactly as the plan doc anticipated:
//   neither `foundry-for-good` nor `ema` exposed ANY phone field at all
//   on initial load; `everai` exposed one, but as an ordinary per-org
//   custom question with an opaque UUID name (`dd4dc7a2-...`), not a
//   `_systemfield_phone`. No org tested actually used a `_systemfield_
//   phone` id, so the phone is NOT looked up by a fixed id: see
//   `findPhoneInput` below, which recognises it by what the control is
//   (`type="tel"`, an `autocomplete` of `tel`, or a phone label) and
//   leaves it alone, saying so, when that does not pick out one box.
// - Ashby has NO structural separation between an EEO/demographic
//   question and an ordinary custom one -- confirmed live: a pronoun
//   question ("What are your preferred pronouns?") and a mundane
//   logistics question ("What is your current notice period?") on the
//   same real posting (everai) sit inside the IDENTICAL container shape
//   (`.ashby-application-form-field-entry` under `.ashby-application-
//   form-section-container`), sharing the same section-id prefix in
//   their `data-field-entry-id`. Unlike Lever's `eeo[`/`surveysResponses[`
//   namespaces and Greenhouse's `#demographic-section`/`.eeoc__container`,
//   there is no platform-level DOM signal here to exclude an EEO-shaped
//   question by construction. D6 is upheld here entirely by TYPE, not by
//   namespace: every genuinely observed EEO-adjacent question on real
//   postings (pronouns, work-authorization, disability accommodations
//   as a *choice*) was a radio/checkbox, landing in `kind: "radio"`
//   (human-only, never LLM-drafted) purely because of its DOM shape --
//   but a real, disclosed limitation follows from this: an org that
//   phrased an EEO-adjacent question as free text (rather than a
//   choice) would have no structural signal excluding it from `kind:
//   "text"`, unlike Lever/Greenhouse. This is a genuine cold-start gap
//   for this ATS specifically, not silently papered over -- closing it
//   properly would need the same signed, human-curated allowlist
//   mechanism E3c already built for Lever, extended to Ashby (a future
//   phase, out of scope here since this repo never curates or signs a
//   real Ashby map).
// - No stable cover-letter selector or naming convention was found live
//   on either tested posting (neither exposed a cover-letter upload at
//   all) -- so there is no guessed id here either. `findCoverLetterSlot`
//   below recognises the slot by what it is: a file input whose own
//   question title says "cover letter". When the form has no such slot,
//   or more than one, the fill says so rather than staying silent.
//
// Per this task's explicit instruction, this file is built exactly like
// Lever's lib/lever.ts was between E2 and E3c: fully open-source,
// unsigned, and functionally complete without any signed field map (this
// repo never curates or signs a real Ashby map -- that's a maintainer/
// secret-custody step, out of scope here).
export const GENERIC_FIELD_DEFAULTS: {
  detectionSelector: string;
  resumeSelector: string;
  standardFields: StandardFieldSpec[];
} = {
  detectionSelector: "#_systemfield_name, #_systemfield_resume",
  resumeSelector: "#_systemfield_resume",
  standardFields: [
    { field: "name", selector: "#_systemfield_name", strategy: "direct", profileFields: ["name"] },
    { field: "email", selector: "#_systemfield_email", strategy: "direct", profileFields: ["email"] },
    // No phone entry: Ashby has no phone id this build has seen (file-level note above).
    // `findPhoneInput` finds it by what it is.
  ],
};

export function isAshbyApplyForm(doc: Document): boolean {
  return doc.querySelector(GENERIC_FIELD_DEFAULTS.detectionSelector) !== null;
}

const SYSTEMFIELD_PREFIX = "_systemfield_";
const FIELD_ENTRY_SELECTOR = "[data-field-path]";
const QUESTION_LABEL_SELECTOR = ".ashby-application-form-question-title";

const QUESTION_DESCRIPTION_SELECTOR = ".ashby-application-form-question-description";
const SECTION_SELECTOR = ".ashby-application-form-section-container";

export interface CustomQuestion {
  fieldName: string;
  label: string | null;
  kind: QuestionKind;
}

function labelForFieldEntry(entry: Element): { label: string | null; sensitive: boolean } {
  return readQuestionLabel(entry.querySelector(QUESTION_LABEL_SELECTOR)?.textContent);
}

// D6 -- the self-ID wording doesn't always live in the question title. An
// entry can be titled "Optional" or "Tell us more" while its description
// says "Voluntary self-identification: how do you describe your ethnic
// background?", or sit under a "Voluntary Self Identification" section
// heading. So the description and the section's own heading are checked
// too, not just the title.
function isSensitiveEntryContext(entry: Element): boolean {
  const description = entry.querySelector(QUESTION_DESCRIPTION_SELECTOR)?.textContent;
  const heading = entry.closest(SECTION_SELECTOR)?.querySelector("h1, h2, h3, h4")?.textContent;
  return isSensitiveSelfIdText(description) || isSensitiveSelfIdText(heading);
}

// D6 fix (2026-09-13), hardened in E6 -- the label-text substitute for the
// structural namespace Lever's `eeo[` prefix and Greenhouse's
// `#demographic-section`/`.eeoc__container` give those two ATSs for free
// (see the file-level note above: Ashby has no such signal at all). The
// classifier itself now lives in lib/questionSafety.ts, shared by all
// three engines: the original regex here missed most realistic phrasings
// ("ethnic origin", "Veterans", "LGBTQ+", "reasonable adjustments", "date
// of birth"...), was bypassed by zero-width/fullwidth/accented/homoglyph
// spellings of a tenant-controlled label, and failed OPEN on a label it
// couldn't read. Scope is unchanged: genuine VOLUNTARY SELF-IDENTIFICATION
// data, not work-authorization/visa/sponsorship questions (a maintainer
// decision; the tests pin it). The real, directly-observed question that
// first surfaced this gap -- everai's "Do you require any accommodations
// or support during the interview(s)..." with no word matching "disab" --
// is why accommodation wording is in the vocabulary.

/**
 * Confirmed live: every genuine field on an Ashby application form --
 * systemfield or custom question alike -- sits inside an element
 * carrying `data-field-path="<canonical-field-id>"` (the field's own
 * UUID for a custom question, or `_systemfield_<name>` for a platform
 * field). An element with no such ancestor (the reCAPTCHA response
 * textarea, an internal "autofill from resume" upload helper distinct
 * from the real `_systemfield_resume` input) is never a real question --
 * excluded by construction, the same "only ever an element inside the
 * platform's own namespace" rule Lever's `cards[` prefix and
 * Greenhouse's `question_<id>` pattern already establish, just keyed on
 * an attribute instead of an id/name prefix here.
 *
 * Deduplicated by `data-field-path` (not by the input's own `name`/`id`,
 * which for a radio/checkbox GROUP is a compound
 * `{sectionId}_{fieldId}` shared by every option, or -- for a multi-
 * select checkbox group -- is the individual OPTION's own label text,
 * confirmed live on a real "how did you hear about us" question --
 * neither is a stable per-question key the way `data-field-path` is).
 */
export function extractCustomQuestions(
  doc: Document,
  excludeFieldNames: string | readonly string[] | null = null,
): CustomQuestion[] {
  const excluded = new Set(excludeFieldNames === null ? [] : [excludeFieldNames].flat());
  const seen = new Set<string>();
  const questions: CustomQuestion[] = [];
  for (const element of doc.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
    "input, textarea, select",
  )) {
    const entry = element.closest(FIELD_ENTRY_SELECTOR);
    if (entry === null) continue;
    const fieldPath = entry.getAttribute("data-field-path");
    if (fieldPath === null || fieldPath.startsWith(SYSTEMFIELD_PREFIX)) continue;
    if (seen.has(fieldPath) || excluded.has(fieldPath)) continue;
    seen.add(fieldPath);
    const { label, sensitive } = labelForFieldEntry(entry);
    if (sensitive || isSensitiveEntryContext(entry)) continue;
    questions.push({ fieldName: fieldPath, label, kind: questionKind(element, label) });
  }
  return questions;
}

/**
 * The one path a value chosen off-page (a saved answer, an LLM draft)
 * ever reaches a real Ashby field -- same multi-layered defense-in-depth
 * shape as Lever's and Greenhouse's own `fillCustomTextAnswer`. Ashby's
 * text/textarea questions carry `name` (and `id`) equal to their own
 * `data-field-path` UUID directly (confirmed live), so this can select
 * by name the same way Lever's version does; the extra `data-field-path`
 * re-check below is a second, independent guard specific to Ashby's
 * shape, since a crafted `fieldName` matching some unrelated element by
 * coincidence would still need to sit inside a matching field-entry
 * container to be accepted.
 *
 * `force` (E6 continuation) -- mirrors Lever's and Greenhouse's own
 * `fillCustomTextAnswer`: bypasses ONLY the D5 not-empty check at the
 * bottom. Every refusal above it (systemfield namespace, missing/
 * mismatched field-entry container, D6 sensitive label or context, combo/
 * non-text control kind) runs unconditionally first and is never affected
 * by `force`.
 */
export function fillCustomTextAnswer(
  doc: Document,
  fieldName: string,
  value: string,
  force = false,
): FillAnswerOutcome {
  if (fieldName.startsWith(SYSTEMFIELD_PREFIX)) return "refused";
  const element = doc.querySelector<HTMLElement>(`[name="${CSS.escape(fieldName)}"]`);
  if (element === null || element.getAttribute("name") !== fieldName) return "refused";
  const entry = element.closest(FIELD_ENTRY_SELECTOR);
  if (entry === null || entry.getAttribute("data-field-path") !== fieldName) return "refused";
  // Same label rules as extraction (D6), re-run on the write path.
  const { label, sensitive } = labelForFieldEntry(entry);
  if (label === null || sensitive || isSensitiveEntryContext(entry)) return "refused";
  if (element.getAttribute("role") === "combobox" || !isTextEntryElement(element)) return "refused";
  // D5: never clobber text that's already there, unless the human
  // explicitly asked to replace it (`force`).
  if (hasExistingText(element) && !force) return "not_empty";

  setReactControlledValue(element, value);
  return "filled";
}


// ---- the phone number and the cover-letter upload, found by what they are --------------

// The WHOLE title has to be a phone label ("Phone", "Mobile phone", "Your phone number",
// "Phone (optional second)"), not merely contain the word: a question that mentions a phone
// ("When are you available for a phone screen?", "Mobile app development experience") is a
// question for the person, and must not get their number typed into it.
const PHONE_LABEL =
  /^(?:(?:your|my|best|primary|preferred|personal|contact)\s+)*(?:(?:mobile|cell(?:ular)?|contact|home|work|daytime)\s+)?(?:phone|telephone|mobile|cell(?:ular)?)(?:\s+(?:number|no\.?|#))?\s*(?:\([^)]{0,40}\))?\s*[:*]?\s*\*?$/iu;
// A phone number that is somebody else's: not the candidate's own.
const NOT_THE_CANDIDATES_PHONE =
  /\b(?:emergency|reference|referee|referrer|manager|supervisor|employer|spouse|partner|parent|guardian|relative|recruiter)\b/iu;
const COVER_LETTER_LABEL = /\bcover[\s-]?letter\b/iu;

function titleOf(entry: Element): string | null {
  return readQuestionLabel(entry.querySelector(QUESTION_LABEL_SELECTOR)?.textContent).label;
}

/** `type="tel"` or an `autocomplete` token that starts with "tel" ("tel", "tel-national"). */
function declaresTelephone(input: HTMLInputElement): boolean {
  if (input.type === "tel") return true;
  return (input.getAttribute("autocomplete") ?? "")
    .toLowerCase()
    .split(/\s+/u)
    .some((token) => token === "tel" || token.startsWith("tel-"));
}

export type PhoneInputResult =
  | { status: "found"; input: HTMLInputElement; fieldPath: string }
  | { status: "none" }
  | { status: "ambiguous" };

/**
 * The candidate's phone box, recognised by what it is, never by a fixed id. Every text input
 * inside a field entry (other than the name, email and résumé system fields, and never a
 * combobox such as a country-code picker) is weighed: one point for declaring a telephone
 * (`type="tel"` or `autocomplete="tel..."`) and one for a title that is, as a whole, a phone
 * label. A box titled as somebody else's number ("Emergency contact phone") is never a
 * candidate. The highest-scoring box wins only when it is the only one with that score; two
 * equally good boxes are `ambiguous`, and the caller leaves both empty and says so. A form
 * with no such box is `none`. Every input of an entry competes, so a text box beside a real
 * tel box in the same entry (a country code, say) cannot shadow it.
 */
export function findPhoneInput(doc: Document): PhoneInputResult {
  const scored: { input: HTMLInputElement; fieldPath: string; score: number }[] = [];
  for (const input of doc.querySelectorAll<HTMLInputElement>("input")) {
    if (!isTextEntryElement(input) || input.type === "hidden") continue;
    if (input.getAttribute("role") === "combobox") continue;
    const entry = input.closest(FIELD_ENTRY_SELECTOR);
    const fieldPath = entry?.getAttribute("data-field-path") ?? null;
    if (entry === null || fieldPath === null || fieldPath.startsWith(SYSTEMFIELD_PREFIX)) continue;
    const label = titleOf(entry);
    if (label !== null && NOT_THE_CANDIDATES_PHONE.test(label)) continue;
    const score = (declaresTelephone(input) ? 1 : 0) + (label !== null && PHONE_LABEL.test(label) ? 1 : 0);
    if (score === 0) continue;
    scored.push({ input, fieldPath, score });
  }
  if (scored.length === 0) return { status: "none" };
  const best = Math.max(...scored.map((c) => c.score));
  const top = scored.filter((c) => c.score === best);
  const only = top[0];
  return top.length === 1 && only !== undefined
    ? { status: "found", input: only.input, fieldPath: only.fieldPath }
    : { status: "ambiguous" };
}

export interface PhonePlan {
  /** The write to make, when there is one. */
  item: FieldFillPlanItem | null;
  /** The field entry the phone box sits in, so it is not also listed as an unanswered question. */
  fieldPath: string | null;
  /** Why the phone was left alone, when the person should know. */
  skipped: string | null;
}

// A selector counts only if it finds this very input: a page can give a wrapper the same id, or
// two boxes the same name, and writing to whatever that happens to resolve to is not the box that
// was found.
function selectorFor(input: HTMLInputElement): string | null {
  const root = input.ownerDocument;
  const candidates: string[] = [];
  if (input.id !== "") candidates.push(`#${CSS.escape(input.id)}`);
  const name = input.getAttribute("name");
  if (name !== null && name !== "") candidates.push(`input[name="${CSS.escape(name)}"]`);
  return candidates.find((candidate) => root.querySelector(candidate) === input) ?? null;
}

/** What to do about the phone box on this form. D5: a box that already holds text is left
 * alone unless `forceRefillAll`. Nothing is said when there is nothing to say: no box, no
 * phone in the profile, or a box already filled. A box this call did not fill (the profile has
 * no phone) is not reported as handled, so it stays in the list of questions left for the
 * person. */
export function planPhoneFill(doc: Document, phone: string | null, forceRefillAll: boolean): PhonePlan {
  const found = findPhoneInput(doc);
  if (found.status === "none") return { item: null, fieldPath: null, skipped: null };
  if (found.status === "ambiguous") {
    return {
      item: null,
      fieldPath: null,
      skipped: phone === null || phone === "" ? null : "more than one box on this form could be your phone number",
    };
  }
  const { input, fieldPath } = found;
  if (phone === null || phone === "") return { item: null, fieldPath: null, skipped: null };
  if (!forceRefillAll && input.value.trim() !== "") return { item: null, fieldPath, skipped: null };
  const selector = selectorFor(input);
  if (selector === null) {
    return { item: null, fieldPath: null, skipped: "the phone box on this form has no id or name that points only at it" };
  }
  return { item: { selector, value: phone }, fieldPath, skipped: null };
}

export type CoverLetterSlotResult =
  | { status: "found"; input: HTMLInputElement; fieldPath: string }
  | { status: "none" }
  | { status: "ambiguous" };

/**
 * The cover-letter upload, recognised by what it is: a file input, in a field entry of its
 * own (never the résumé system field), whose question title says "cover letter". Exactly one
 * is `found`; none is `none`; more than one is `ambiguous` and nothing is attached.
 */
export function findCoverLetterSlot(doc: Document): CoverLetterSlotResult {
  const slots: { input: HTMLInputElement; fieldPath: string }[] = [];
  const seenEntries = new Set<Element>();
  for (const input of doc.querySelectorAll<HTMLInputElement>('input[type="file"]')) {
    const entry = input.closest(FIELD_ENTRY_SELECTOR);
    const fieldPath = entry?.getAttribute("data-field-path") ?? null;
    if (entry === null || fieldPath === null || fieldPath === `${SYSTEMFIELD_PREFIX}resume`) continue;
    const { label, sensitive } = labelForFieldEntry(entry);
    if (label === null || sensitive || !COVER_LETTER_LABEL.test(label)) continue;
    if (seenEntries.has(entry)) continue;
    seenEntries.add(entry);
    slots.push({ input, fieldPath });
  }
  const only = slots[0];
  if (only === undefined) return { status: "none" };
  return slots.length === 1 ? { status: "found", ...only } : { status: "ambiguous" };
}
