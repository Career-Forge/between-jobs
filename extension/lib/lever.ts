import type { ExtensionPersonalInfo } from "./types";

// Field selectors confirmed live against a real posting (jobs.lever.co/
// theathletic, 2026-09-07/08) via direct DOM inspection, not just the
// earlier research pass's own summary -- Lever's apply form is
// server-rendered HTML (not a React SPA), so a plain `.value` set plus a
// dispatched `input`/`change` event is sufficient; no synthetic-event
// trick for a controlled component is needed here (that problem is
// Greenhouse/Ashby's, deferred to E4/E5).
export function isLeverApplyForm(doc: Document): boolean {
  return doc.querySelector('form input[name="resume"]') !== null;
}

interface StandardField {
  selector: string;
  getValue: (info: ExtensionPersonalInfo) => string | null;
}

// Deliberately does NOT include `org` (current company) -- ExtensionPersonalInfo
// has no such field (between-jobs' own profile schema doesn't track it),
// so it's left for the human to fill rather than guessed at.
export const STANDARD_FIELDS: readonly StandardField[] = [
  { selector: 'input[name="name"]', getValue: (info) => info.name },
  { selector: 'input[name="email"]', getValue: (info) => info.email },
  { selector: 'input[name="phone"]', getValue: (info) => info.phone },
  {
    selector: 'input[name="location"]',
    getValue: (info) => {
      const parts = [info.location.city, info.location.region, info.location.country].filter(
        (part) => part.length > 0,
      );
      return parts.length > 0 ? parts.join(", ") : null;
    },
  },
  { selector: 'input[name="urls[LinkedIn]"]', getValue: (info) => info.linkedin || null },
  {
    selector: 'input[name="urls[Other (portfolio, GitHub etc)]"]',
    getValue: (info) => info.portfolio || info.github || null,
  },
];

export interface FieldFillPlanItem {
  selector: string;
  value: string;
}

/**
 * D5 (browser-extension.md) -- Fill is a repeatable, idempotent action
 * against a fresh DOM read: a field already holding a non-empty value
 * (whether the user typed it or a prior Fill set it) is skipped unless
 * `forceRefillAll` is passed, matching the "skip hand-edited fields,
 * don't clobber" decision. Pure with respect to the DOM (only reads
 * `.value`), so this is the part covered by real unit tests; the actual
 * mutation happens in `applyFillPlan`.
 */
export function planStandardFieldFills(
  doc: Document,
  personalInfo: ExtensionPersonalInfo,
  forceRefillAll: boolean,
): FieldFillPlanItem[] {
  const plan: FieldFillPlanItem[] = [];
  for (const field of STANDARD_FIELDS) {
    const element = doc.querySelector<HTMLInputElement>(field.selector);
    if (element === null) continue;
    if (!forceRefillAll && element.value.trim() !== "") continue;
    const value = field.getValue(personalInfo);
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
  kind: "text" | "file";
}

// Confirmed live: `.application-field` is the per-card wrapper around a
// question's actual input(s); its own PARENT div holds `.application-label`
// as a direct sibling -- a clean 1:1 mapping (verified: exactly one
// `.application-field` per label-holding parent), not something that
// needs fuzzy nearest-ancestor guessing.
function labelForCardField(element: Element): string | null {
  const appField = element.closest(".application-field");
  const label = appField?.parentElement?.querySelector(".application-label");
  return label?.textContent?.trim().replace(/\s+/g, " ") ?? null;
}

// Only `cards[<uuid>][...]` fields are genuine per-org custom application
// questions eligible for known-question-memory matching (E3). Lever's
// `eeo[...]` (gender/race/veteran) and `surveysResponses[<uuid>][...]`
// (a separate voluntary demographic survey) are excluded BY CONSTRUCTION
// here -- confirmed live these are real, separate field-name namespaces,
// not something requiring a label-text sensitivity classifier (D6, and
// the selector-map design note on excluding consent/sensitive fields by
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
export function extractCustomQuestions(
  doc: Document,
  excludeFieldName: string | null = null,
): CustomQuestion[] {
  const seen = new Set<string>();
  const questions: CustomQuestion[] = [];
  for (const element of doc.querySelectorAll<HTMLInputElement>('form [name^="cards["]')) {
    const name = element.getAttribute("name");
    if (name === null || seen.has(name) || element.type === "hidden" || name === excludeFieldName) {
      continue;
    }
    seen.add(name);
    questions.push({
      fieldName: name,
      label: labelForCardField(element),
      kind: element.type === "file" ? "file" : "text",
    });
  }
  return questions;
}

// Confirmed live (a real Sysdig posting, 2026-09-08): unlike Lever's
// standard fields, a cover-letter upload -- when an org enables one at
// all -- is NOT a stable, predictable selector. It's the exact same
// opaque per-org `cards[<uuid>]` custom-field pattern as an ordinary
// text question, just with `type="file"`, discoverable only by matching
// its rendered "Cover Letter" label at runtime -- the same mechanism
// already proven for custom-question labels, not a new one.
export function findCoverLetterField(doc: Document): string | null {
  for (const element of doc.querySelectorAll<HTMLInputElement>('form [name^="cards["][type="file"]')) {
    const label = labelForCardField(element);
    if (label !== null && /cover letter/i.test(label)) {
      return element.getAttribute("name");
    }
  }
  return null;
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
