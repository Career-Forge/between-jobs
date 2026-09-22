import type { StandardFieldSpec } from "./ats-field-map";
import {
  hasExistingText,
  isTextEntryElement,
  questionKind,
  readQuestionLabel,
  type FillAnswerOutcome,
  type QuestionKind,
} from "./questionSafety";

// E4/E5 -- `planStandardFieldFills`/`applyFillPlan`/`attachFile` used to
// be defined directly in this file; they were always genuinely ATS-
// agnostic (no Lever-specific logic anywhere in them), so they moved to
// lib/standardFields.ts once Greenhouse/Ashby needed the same planning
// step (just a different, React-aware apply step for those two -- see
// `applyReactControlledFillPlan` there). Re-exported here verbatim so
// this file's own existing call sites and tests (which import these
// names from "@/lib/lever") keep working unchanged.
export { applyFillPlan, attachFile, planStandardFieldFills } from "./standardFields";
export type { FieldFillPlanItem } from "./standardFields";

// Field selectors confirmed live against a real posting (jobs.lever.co/
// theathletic, 2026-09-07/08) via direct DOM inspection, not just the
// earlier research pass's own summary -- Lever's apply form is
// server-rendered HTML (not a React SPA), so a plain `.value` set plus a
// dispatched `input`/`change` event is sufficient; no synthetic-event
// trick for a controlled component is needed here (that problem is
// Greenhouse/Ashby's, deferred to E4/E5).
//
// GENERIC_FIELD_DEFAULTS (E3c) -- deliberately open-source and unsigned,
// NOT part of the E3c signed field map. `name`/`email`/`phone` and the
// résumé-upload selector are plain, unremarkable HTML-form conventions
// any ATS's markup could plausibly use, not curated Lever-specific IP --
// unlike the genuinely Lever-idiosyncratic conventions (the urls[...]
// field-name shape, the cards[ custom-question prefix, the application-
// field/application-label DOM-nesting shape, the cover-letter label
// pattern), which live ONLY in the signed map (extension/lib/
// ats-field-map.ts) and are never bundled here. This split means a fresh
// self-hosted clone gets baseline autofill with zero setup -- matching
// this repo's own "BYOK-first... no demo shells" rule -- and also means
// D4's fail-closed guarantee is scoped to what the signed map actually
// protects: a field-map outage disables the Lever-idiosyncratic behavior
// (custom questions, cover-letter discovery, location/LinkedIn/portfolio)
// but never these four fields, since nothing signed ever backed them.
export const GENERIC_FIELD_DEFAULTS: {
  detectionSelector: string;
  resumeSelector: string;
  standardFields: StandardFieldSpec[];
} = {
  detectionSelector: 'form input[name="resume"]',
  resumeSelector: 'input[name="resume"]',
  standardFields: [
    { field: "name", selector: 'input[name="name"]', strategy: "direct", profileFields: ["name"] },
    { field: "email", selector: 'input[name="email"]', strategy: "direct", profileFields: ["email"] },
    { field: "phone", selector: 'input[name="phone"]', strategy: "direct", profileFields: ["phone"] },
  ],
};

export function isLeverApplyForm(doc: Document): boolean {
  return doc.querySelector(GENERIC_FIELD_DEFAULTS.detectionSelector) !== null;
}

export interface CustomQuestion {
  fieldName: string;
  label: string | null;
  kind: QuestionKind;
}

/** The subset of the verified field map these functions actually need --
 * accepting a narrower type than the full `LeverFieldMap` keeps them
 * trivially testable against a small fixture object, not the whole
 * shape. */
export interface LeverQuestionMapFields {
  custom_question_prefix: string;
  label_wrapper_selector: string;
  label_selector: string;
}

// Confirmed live: the wrapper/label selectors' own DOM-nesting SHAPE
// (`.closest(wrapper)` then `.parentElement.querySelector(label)`) is
// Lever's structural convention -- a clean 1:1 mapping (verified:
// exactly one wrapper element per label-holding parent), not something
// that needs fuzzy nearest-ancestor guessing. The wrapper/label
// SELECTOR STRINGS themselves are E3c signed-map data (map.label_
// wrapper_selector/map.label_selector); this traversal shape is open-
// source algorithm, parameterized on them.
function labelForCardField(
  element: Element,
  wrapperSelector: string,
  labelSelector: string,
): { label: string | null; sensitive: boolean } {
  const wrapper = element.closest(wrapperSelector);
  const label = wrapper?.parentElement?.querySelector(labelSelector);
  return readQuestionLabel(label?.textContent);
}

// Only fields under the map's own `custom_question_prefix` (Lever:
// `cards[<uuid>][...]`) are genuine per-org custom application questions
// eligible for known-question-memory matching (E3). Lever's `eeo[...]`
// (gender/race/veteran) and `surveysResponses[<uuid>][...]` (a separate
// voluntary demographic survey) are excluded BY CONSTRUCTION here --
// confirmed live these are real, separate field-name namespaces. That
// covers what LEVER renders as demographic, not what a TENANT authors: an
// org can put "What is your gender identity?" in the ordinary `cards[`
// namespace, so every card is also label-checked (E6, lib/questionSafety.ts)
// and a sensitive one is excluded the same way.
//
// `excludeFieldName` is the field `findCoverLetterField` already
// identified as the cover-letter slot -- skipped here since content.ts
// handles it separately. Adversarially confirmed a real gap in an
// earlier version of this function: it used to exclude EVERY file-type
// card unconditionally, which meant a real "additional portfolio file"
// style field (confirmed present on real postings) went completely
// unmentioned anywhere -- not attached, not listed as needing attention,
// silently invisible right up until the person hit Lever's own Submit
// and it blocked with no explanation from this extension. Now any OTHER
// file-type field is still returned (tagged `kind: "file"`) so the side
// panel can at least tell the person it exists and needs their own
// upload, even though this extension can't fill it for them.
//
// `kind: "radio"` (E3b) is its own category, distinct from "text" --
// binary-choice questions are disproportionately the sensitive ones
// (work authorization, sponsorship) and don't fit a "draft prose"
// generation model regardless. The LLM-answer feature only ever
// operates on `kind: "text"` fields; radio questions always stay
// human-only, same as file fields do today. E6 made this fail closed:
// `text` is only ever a textarea or a text-like input, so a native
// <select> (how Lever renders a Dropdown question) is `radio`, not text.
export function extractCustomQuestions(
  doc: Document,
  map: LeverQuestionMapFields,
  excludeFieldName: string | null = null,
): CustomQuestion[] {
  const seen = new Set<string>();
  const questions: CustomQuestion[] = [];
  const selector = `form [name^="${CSS.escape(map.custom_question_prefix)}"]`;
  for (const element of doc.querySelectorAll<HTMLInputElement>(selector)) {
    const name = element.getAttribute("name");
    if (name === null || seen.has(name) || element.type === "hidden" || name === excludeFieldName) {
      continue;
    }
    seen.add(name);
    const { label, sensitive } = labelForCardField(element, map.label_wrapper_selector, map.label_selector);
    // D6, by label: the `cards[` namespace is tenant-authored, so an org
    // can put a gender/disability/accommodation question in it. Excluded
    // outright, exactly like the `eeo[`/`surveysResponses[` namespaces
    // above -- see lib/questionSafety.ts.
    if (sensitive) continue;
    questions.push({ fieldName: name, label, kind: questionKind(element, label) });
  }
  return questions;
}

// Confirmed live (a real Sysdig posting, 2026-09-08): unlike Lever's
// standard fields, a cover-letter upload -- when an org enables one at
// all -- is NOT a stable selector. It's the exact same opaque per-org
// custom-field pattern as an ordinary text question, just with
// `type="file"`, discoverable only by matching its rendered label at
// runtime against the map's own `cover_letter_label_pattern` -- the same
// mechanism already proven for custom-question labels, not a new one.
export function findCoverLetterField(
  doc: Document,
  map: LeverQuestionMapFields & { cover_letter_label_pattern: string },
): string | null {
  const selector = `form [name^="${CSS.escape(map.custom_question_prefix)}"][type="file"]`;
  const pattern = new RegExp(map.cover_letter_label_pattern, "i");
  for (const element of doc.querySelectorAll<HTMLInputElement>(selector)) {
    const { label } = labelForCardField(element, map.label_wrapper_selector, map.label_selector);
    if (label !== null && pattern.test(label)) {
      return element.getAttribute("name");
    }
  }
  return null;
}

/**
 * E3b's known-question-memory/LLM-drafted-answer fill path -- the ONE
 * place a value chosen off-page (a saved answer, an LLM draft) ever
 * reaches the real DOM, so it re-validates the target itself rather than
 * trusting the caller already filtered correctly (the same defense-in-
 * depth precedent K1's own dual Pydantic+DB status enforcement already
 * set). Refuses to touch anything that isn't a genuine custom-question
 * text/textarea field: never a radio/checkbox (E3b never generates
 * binary-choice answers to begin with), never a file input, never
 * `eeo[...]`/`surveysResponses[...]`, never a standard field, and never
 * anything resembling a submit control. Returns false, touching nothing,
 * for any field it won't fill.
 *
 * `fieldName` originates from the page's own `name` attribute (via
 * extractCustomQuestions) -- untrusted, scraped content per this
 * project's own rule. `CSS.escape` prevents a crafted name containing a
 * `"` from breaking out of the attribute selector into a second,
 * unrelated one; re-checking the matched element's own `name` against
 * `fieldName` afterward is a second, independent guard against the same
 * class of mistargeting even if the selector construction is ever wrong
 * in some other way -- not exploitable against Lever's real UUID-only
 * field names today, but cheap, defense-in-depth insurance regardless.
 *
 * `force` (E6 continuation) -- the side panel's per-question "Replace"
 * action, the only escape hatch for D5's "not_empty" outcome on a custom
 * question ("Refill all" explicitly never touches these -- the per-
 * question FILL_FIELD path threads no such parameter today). It bypasses
 * ONLY the D5 not-empty check below, nothing else: the namespace check,
 * the element-kind check, and the D6 sensitive-label check above all run
 * first and still refuse unconditionally regardless of `force` -- there
 * is no code path where `force: true` can make a sensitive or unmatched
 * field fillable.
 */
export function fillCustomTextAnswer(
  doc: Document,
  map: LeverQuestionMapFields,
  fieldName: string,
  value: string,
  force = false,
): FillAnswerOutcome {
  if (!fieldName.startsWith(map.custom_question_prefix)) return "refused";
  const element = doc.querySelector<HTMLInputElement | HTMLTextAreaElement>(
    `form [name="${CSS.escape(fieldName)}"]`,
  );
  if (element === null || element.getAttribute("name") !== fieldName) return "refused";
  if (!isTextEntryElement(element)) return "refused";

  // The same label rules extraction applies (D6), re-run here because this
  // is the write path: a sensitive label -- or one that can't be read, so
  // can't be shown to be safe -- is refused even if a caller asks. The map's
  // selectors are signed data, but a typo in one throws; that must not
  // become a write.
  let labelInfo: { label: string | null; sensitive: boolean };
  try {
    labelInfo = labelForCardField(element, map.label_wrapper_selector, map.label_selector);
  } catch {
    return "refused";
  }
  if (labelInfo.label === null || labelInfo.sensitive) return "refused";

  // D5: never clobber text that's already there, unless the human
  // explicitly asked to replace it (`force`).
  if (hasExistingText(element) && !force) return "not_empty";

  element.value = value;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return "filled";
}
