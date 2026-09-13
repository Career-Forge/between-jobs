import type { StandardFieldSpec } from "./ats-field-map";
import { setReactControlledValue } from "./standardFields";

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
//   phone` id, so `#_systemfield_phone` below is an inferred, best-
//   effort attempt at the same naming convention the other three
//   systemfields use -- disclosed as unconfirmed live, not claimed as
//   verified. It's a harmless no-op (skipped by `planStandardFieldFills`
//   like any other absent selector) on every org that doesn't use it.
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
//   all) -- rather than guess a `_systemfield_coverLetter`-shaped name
//   with zero live confirmation, this build simply doesn't attempt
//   Ashby cover-letter attach yet. Disclosed as a real gap, not a silent
//   omission.
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
    // See the file-level note above -- inferred from Ashby's own naming
    // convention, not directly observed live on any tested org.
    { field: "phone", selector: "#_systemfield_phone", strategy: "direct", profileFields: ["phone"] },
  ],
};

export function isAshbyApplyForm(doc: Document): boolean {
  return doc.querySelector(GENERIC_FIELD_DEFAULTS.detectionSelector) !== null;
}

const SYSTEMFIELD_PREFIX = "_systemfield_";
const FIELD_ENTRY_SELECTOR = "[data-field-path]";
const QUESTION_LABEL_SELECTOR = ".ashby-application-form-question-title";

export interface CustomQuestion {
  fieldName: string;
  label: string | null;
  kind: "text" | "file" | "radio";
}

function labelForFieldEntry(entry: Element): string | null {
  const label = entry.querySelector(QUESTION_LABEL_SELECTOR);
  return label?.textContent?.trim().replace(/\s+/g, " ") ?? null;
}

// D6 fix (2026-09-13) -- the label-text substitute for the structural
// namespace Lever's `eeo[` prefix and Greenhouse's `#demographic-section`/
// `.eeoc__container` give those two ATSs for free (see the file-level
// note above: Ashby has no such signal at all). Scoped deliberately
// narrow, to match what Lever/Greenhouse actually exclude -- genuine
// VOLUNTARY SELF-IDENTIFICATION data (gender, sex, race/ethnicity,
// disability status, veteran status, sexual orientation, transgender
// status, preferred pronouns) -- not work-authorization/visa/sponsorship
// questions. Must also catch a EUPHEMISM, not just the literal word
// "disability": the real, directly-observed (not hypothetical) posting
// that first surfaced this gap (jobs.ashbyhq.com/everai) phrases its own
// disability-accommodation question as "Do you require any accommodations
// or support during the interview(s)..." -- no word matching "disab"
// anywhere in it. "accommodat" is included below specifically because of
// this real example, not a hypothetical one. Confirmed directly from
// Greenhouse's own real markup
// (lib/greenhouse.ts's own comment): a work-authorization/sponsorship
// question there is NOT inside `.eeoc__container` at all -- it's an
// ordinary `question_<id>` custom question, human-only via kind:"radio"
// when it's a yes/no select, or -- when genuinely free text -- flowing
// through the same generate/verify/human-review pipeline every other
// free-text question already does. That pipeline (claim verification +
// severity-tagged warnings in the side panel) is this codebase's actual,
// already-shipped mitigation for a free-text work-authorization answer
// today; a properly-guided source fact is `work-authorization-status.md`'s
// own separate, not-yet-built feature. This function does not attempt
// to re-solve that here -- it closes the narrower, structural gap this
// file's own research disclosed: an org phrasing genuine demographic
// self-ID as free text, which (unlike work-authorization) has no
// existing safety net anywhere in this codebase once it reaches
// `kind: "text"`.
//
// Deliberately over-inclusive, not narrowly tuned: D6's own governing
// principle is "opt-in only," so a false-positive exclusion (a benign
// question that happens to mention "race" or "veteran") costs the user
// one field they answer directly on the page instead of through the
// side panel -- a false negative would mean a real self-ID answer
// reaching an LLM prompt, which is the actual harm D6 exists to prevent.
const DEMOGRAPHIC_SELF_ID_LABEL_PATTERN =
  /\bgender\b|\bsex\b|\bpronouns?\b|\brace\b|\bracial\b|\bethnicit(?:y|ies)\b|\bhispanic\b|\blatino\b|\blatina\b|\btransgender\b|\bveteran\b|\bdisab\w*|\baccommodat\w*|\bsexual orientation\b|\bself-identif\w*/i;

function isDemographicSelfIdLabel(label: string | null): boolean {
  return label !== null && DEMOGRAPHIC_SELF_ID_LABEL_PATTERN.test(label);
}

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
export function extractCustomQuestions(doc: Document, excludeFieldName: string | null = null): CustomQuestion[] {
  const seen = new Set<string>();
  const questions: CustomQuestion[] = [];
  for (const element of doc.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
    "input, textarea, select",
  )) {
    const entry = element.closest(FIELD_ENTRY_SELECTOR);
    if (entry === null) continue;
    const fieldPath = entry.getAttribute("data-field-path");
    if (fieldPath === null || fieldPath.startsWith(SYSTEMFIELD_PREFIX)) continue;
    if (seen.has(fieldPath) || fieldPath === excludeFieldName) continue;
    const label = labelForFieldEntry(entry);
    if (isDemographicSelfIdLabel(label)) continue;
    seen.add(fieldPath);

    const tag = element.tagName;
    const type = tag === "INPUT" ? (element as HTMLInputElement).type : tag === "SELECT" ? "select" : "textarea";
    let kind: CustomQuestion["kind"] = "text";
    if (type === "file") kind = "file";
    else if (type === "radio" || type === "checkbox" || type === "select") kind = "radio";
    questions.push({ fieldName: fieldPath, label, kind });
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
 */
export function fillCustomTextAnswer(doc: Document, fieldName: string, value: string): boolean {
  if (fieldName.startsWith(SYSTEMFIELD_PREFIX)) return false;
  const element = doc.querySelector<HTMLInputElement | HTMLTextAreaElement>(
    `[name="${CSS.escape(fieldName)}"]`,
  );
  if (element === null || element.getAttribute("name") !== fieldName) return false;
  const entry = element.closest(FIELD_ENTRY_SELECTOR);
  if (entry === null || entry.getAttribute("data-field-path") !== fieldName) return false;
  if (isDemographicSelfIdLabel(labelForFieldEntry(entry))) return false;

  const tag = element.tagName;
  const type = tag === "INPUT" ? (element as HTMLInputElement).type : "textarea";
  const isTextLike =
    tag === "TEXTAREA" || (tag === "INPUT" && (type === "text" || type === "email" || type === "tel" || type === "url"));
  if (!isTextLike) return false;

  setReactControlledValue(element, value);
  return true;
}
