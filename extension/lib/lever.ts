import type { StandardFieldSpec } from "./ats-field-map";
import type { ExtensionPersonalInfo } from "./types";

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

export interface FieldFillPlanItem {
  selector: string;
  value: string;
}

function getProfileValue(info: ExtensionPersonalInfo, path: string): string {
  const parts = path.split(".");
  let value: unknown = info;
  for (const part of parts) {
    if (value === null || typeof value !== "object") return "";
    value = (value as Record<string, unknown>)[part];
  }
  return typeof value === "string" ? value : "";
}

/**
 * The declarative interpreter E3c introduced in place of `STANDARD_
 * FIELDS`' old `getValue` closures -- functions can't be part of a
 * signed JSON payload, so this reproduces the same three behaviors a
 * pure, generic vocabulary a signed map (or GENERIC_FIELD_DEFAULTS) can
 * express as plain data: `direct` (a plain field read, treating an
 * empty string as "no value" -- matches the old `info.linkedin || null`
 * shape exactly), `fallback` (first non-empty of several, matching the
 * old `info.portfolio || info.github` shape), and `joinNonEmpty`
 * (matching the old location joiner -- filters empties, joins the rest).
 */
function resolveFieldValue(spec: StandardFieldSpec, info: ExtensionPersonalInfo): string | null {
  switch (spec.strategy) {
    case "direct": {
      const value = getProfileValue(info, spec.profileFields[0] ?? "");
      return value || null;
    }
    case "fallback": {
      for (const path of spec.profileFields) {
        const value = getProfileValue(info, path);
        if (value) return value;
      }
      return null;
    }
    case "joinNonEmpty": {
      const parts = spec.profileFields
        .map((path) => getProfileValue(info, path))
        .filter((value) => value.length > 0);
      return parts.length > 0 ? parts.join(spec.separator ?? ", ") : null;
    }
  }
}

/**
 * D5 (browser-extension.md) -- Fill is a repeatable, idempotent action
 * against a fresh DOM read: a field already holding a non-empty value
 * (whether the user typed it or a prior Fill set it) is skipped unless
 * `forceRefillAll` is passed, matching the "skip hand-edited fields,
 * don't clobber" decision. Pure with respect to the DOM (only reads
 * `.value`), so this is the part covered by real unit tests; the actual
 * mutation happens in `applyFillPlan`.
 *
 * `standardFields` (E3c) is caller-supplied -- typically
 * `[...GENERIC_FIELD_DEFAULTS.standardFields, ...(verifiedMap?.standard_
 * fields ?? [])]` -- rather than a hardcoded module-level constant, so
 * this function has zero knowledge of which entries came from the
 * signed map versus the open-source defaults.
 */
export function planStandardFieldFills(
  doc: Document,
  personalInfo: ExtensionPersonalInfo,
  forceRefillAll: boolean,
  standardFields: readonly StandardFieldSpec[],
): FieldFillPlanItem[] {
  const plan: FieldFillPlanItem[] = [];
  for (const field of standardFields) {
    const element = doc.querySelector<HTMLInputElement>(field.selector);
    if (element === null) continue;
    if (!forceRefillAll && element.value.trim() !== "") continue;
    const value = resolveFieldValue(field, personalInfo);
    if (value === null || value === "") continue;
    plan.push({ selector: field.selector, value });
  }
  return plan;
}

export function applyFillPlan(doc: Document, plan: readonly FieldFillPlanItem[]): string[] {
  const filled: string[] = [];
  for (const item of plan) {
    const element = doc.querySelector<HTMLInputElement>(item.selector);
    if (element === null) continue;
    element.value = item.value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    filled.push(item.selector);
  }
  return filled;
}

export interface CustomQuestion {
  fieldName: string;
  label: string | null;
  kind: "text" | "file" | "radio";
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
function labelForCardField(element: Element, wrapperSelector: string, labelSelector: string): string | null {
  const wrapper = element.closest(wrapperSelector);
  const label = wrapper?.parentElement?.querySelector(labelSelector);
  return label?.textContent?.trim().replace(/\s+/g, " ") ?? null;
}

// Only fields under the map's own `custom_question_prefix` (Lever:
// `cards[<uuid>][...]`) are genuine per-org custom application questions
// eligible for known-question-memory matching (E3). Lever's `eeo[...]`
// (gender/race/veteran) and `surveysResponses[<uuid>][...]` (a separate
// voluntary demographic survey) are excluded BY CONSTRUCTION here --
// confirmed live these are real, separate field-name namespaces, not
// something requiring a label-text sensitivity classifier (D6, and the
// selector-map design note on excluding consent/sensitive fields by
// authoring rather than runtime detection).
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
// human-only, same as file fields do today.
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
    let kind: CustomQuestion["kind"] = "text";
    if (element.type === "file") kind = "file";
    else if (element.type === "radio" || element.type === "checkbox") kind = "radio";
    questions.push({
      fieldName: name,
      label: labelForCardField(element, map.label_wrapper_selector, map.label_selector),
      kind,
    });
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
    const label = labelForCardField(element, map.label_wrapper_selector, map.label_selector);
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
 */
export function fillCustomTextAnswer(
  doc: Document,
  map: Pick<LeverQuestionMapFields, "custom_question_prefix">,
  fieldName: string,
  value: string,
): boolean {
  if (!fieldName.startsWith(map.custom_question_prefix)) return false;
  const element = doc.querySelector<HTMLInputElement | HTMLTextAreaElement>(
    `form [name="${CSS.escape(fieldName)}"]`,
  );
  if (element === null || element.getAttribute("name") !== fieldName) return false;
  const tag = element.tagName;
  const type = (element as HTMLInputElement).type;
  const isTextLike = tag === "TEXTAREA" || (tag === "INPUT" && (type === "text" || type === "email"));
  if (!isTextLike) return false;

  element.value = value;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

/**
 * The only working approach for `<input type="file">` -- browsers block
 * scripts from setting `.value` on a file input directly. Constructs a
 * real `File` from bytes already fetched, wraps it in a `DataTransfer`,
 * and dispatches a `change` event the page's own upload-handling JS
 * picks up exactly as if the user had picked a file. Used for both the
 * résumé (a fixed selector) and the cover letter (a runtime-discovered
 * one via `findCoverLetterField`) -- the attach mechanism itself doesn't
 * care which.
 */
export function attachFile(
  input: HTMLInputElement,
  bytes: ArrayBuffer,
  filename: string,
  mimeType: string,
): void {
  const file = new File([bytes], filename, { type: mimeType });
  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(file);
  input.files = dataTransfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}
