import type { StandardFieldSpec } from "./ats-field-map";
import {
  hasExistingText,
  isTextEntryElement,
  questionKind,
  readQuestionLabel,
  type FillAnswerOutcome,
  type QuestionKind,
} from "./questionSafety";
import { setReactControlledValue } from "./standardFields";

// E4 (browser-extension.md) -- confirmed live (not assumed from the
// scoping research's own summary) against three real, unrelated
// job-boards.greenhouse.io postings (Melio, Wheely, Cloudflare) on
// 2026-09-13. Two real corrections to the plan doc's own framing came
// out of that research:
//
// 1. The "two-live-surface wrinkle" (modern job-boards.greenhouse.io
//    React SPA vs a legacy boards.greenhouse.io iframe embed) no longer
//    exists as a distinct code path to handle: every `boards.greenhouse.
//    io/*` URL tested (Figma, Wheely, Cloudflare) 30x-redirected straight
//    to the modern `job-boards.greenhouse.io` React SPA; Point72's own
//    listing had moved entirely off Greenhouse onto a custom domain.
//    Greenhouse appears to have fully consolidated onto the modern
//    surface. `boards.greenhouse.io` is still kept in host_permissions/
//    the content script's own `matches` (defensive -- this was checked
//    against 3 real companies, not all ~16,000 Greenhouse-hosted ones,
//    and a redirect landing on job-boards.greenhouse.io is exactly what
//    this engine already handles), but no separate iframe-embed
//    detection or DOM shape was built, since none was found live.
//
// 2. Unlike Lever's server-rendered form, job-boards.greenhouse.io is a
//    genuine React SPA with React-CONTROLLED inputs -- confirmed by
//    inspecting each field's own `__reactProps$...`/fiber directly, not
//    assumed: a plain `.value =` assignment left React's own internal
//    prop at the OLD value even though the DOM's `.value` visibly
//    changed, and the `setReactControlledValue` native-setter workaround
//    (lib/standardFields.ts) was confirmed live to update both. This is
//    the single hardest new technical piece this ATS introduces, exactly
//    as browser-extension.md's own E4 blurb anticipated.
//
// Field selectors below are Greenhouse's own PLATFORM-rendered ids --
// confirmed byte-identical (first_name/last_name/email/phone/resume/
// cover_letter/country, plus the `question_<numeric-id>` custom-question
// convention and the `#demographic-section`/`.eeoc__container` EEO
// exclusion) across all three real companies tested, none of them
// per-org-customizable the way an org's own label text can be. Per this
// task's own explicit instruction, this file is built exactly like
// Lever's lib/lever.ts was between E2 and E3c: fully open-source and
// unsigned, working correctly with zero dependency on a signed field
// map (this repo never curates or signs a real Greenhouse map -- that's
// a maintainer/secret-custody step, out of scope here). A future
// Greenhouse-specific "E3c equivalent" phase would need to move these
// platform conventions into a signed, hosted map to close the same
// CLAUDE.md "ATS selector maps... belong to the hosted services, not
// this repo" gap E3c already closed for Lever -- named here as a real,
// disclosed follow-up, not silently dropped.
export const GENERIC_FIELD_DEFAULTS: {
  detectionSelector: string;
  resumeSelector: string;
  coverLetterSelector: string;
  standardFields: StandardFieldSpec[];
} = {
  detectionSelector: "#first_name, #resume",
  resumeSelector: "#resume",
  // Confirmed live: unlike Lever's cover letter (an opaque, per-org
  // `cards[<uuid>]` field discoverable only by matching its rendered
  // label at runtime), Greenhouse's cover-letter upload is its own
  // stable, platform-rendered `#cover_letter` file input -- a genuine
  // simplification over Lever, not something this build had to work
  // around. No runtime label-discovery mechanism is needed here.
  coverLetterSelector: "#cover_letter",
  standardFields: [
    { field: "first_name", selector: "#first_name", strategy: "firstNameWord", profileFields: ["name"] },
    { field: "last_name", selector: "#last_name", strategy: "lastNameWord", profileFields: ["name"] },
    { field: "email", selector: "#email", strategy: "direct", profileFields: ["email"] },
    { field: "phone", selector: "#phone", strategy: "direct", profileFields: ["phone"] },
    // Deliberately NOT attempted: `#country` (also live-confirmed
    // present and often required). It's a react-select combobox, not a
    // plain text input -- setting text into its underlying input only
    // changes the search box, it doesn't commit a real selected option,
    // so attempting it would risk leaving a required field in a
    // misleading half-filled state that still fails Greenhouse's own
    // validation. Matches this project's "unknown means labeled as
    // unknown, never guessed" rule: better to leave it genuinely empty
    // (the same native validation the person would hit regardless) than
    // fake-fill it. A real select/combobox interaction mechanism is a
    // separate, harder problem than this build's scope covers.
  ],
};

export function isGreenhouseApplyForm(doc: Document): boolean {
  return doc.querySelector(GENERIC_FIELD_DEFAULTS.detectionSelector) !== null;
}

const QUESTION_ID_PATTERN = /^question_\d+$/;

// Confirmed live: Greenhouse's OWN platform renders exactly two distinct
// EEO/demographic containers, structurally separate from ordinary custom
// questions -- `#demographic-section` (a "diversity survey"-style block
// of bare-numeric-id fields, e.g. gender identity/racial background/
// sexual orientation/transgender/disability/veteran) and
// `.eeoc__container` (the classic EEO block: `#gender`/
// `#hispanic_ethnicity`/`#veteran_status`/`#disability_status`). Neither
// block's fields ever get the `question_<id>` id Greenhouse gives every
// genuine custom application question -- so the primary exclusion is
// already by construction (only `question_`-prefixed ids are ever
// considered a custom question at all), and the container check below is
// deliberate defense-in-depth on top of that, not the only thing
// standing between D6 and a real EEO field.
const EXCLUDED_CONTAINER_SELECTOR = "#demographic-section, .eeoc__container";

export interface CustomQuestion {
  fieldName: string;
  label: string | null;
  kind: QuestionKind;
}

function labelForQuestion(doc: Document, id: string): { label: string | null; sensitive: boolean } {
  const label = doc.querySelector(`label[for="${CSS.escape(id)}"]`);
  return readQuestionLabel(label?.textContent);
}

/**
 * Only elements whose `id` matches Greenhouse's own `question_<numeric>`
 * convention are considered -- confirmed live this is true for every
 * genuine custom application question (free-text, LinkedIn/website,
 * work-authorization/sponsorship yes-no selects, scheduling questions)
 * across all three companies tested, and never true for either EEO/
 * demographic block. `excludeFieldName` is the cover-letter slot when
 * one is being handled separately by the caller (kept for shape parity
 * with Lever's own `extractCustomQuestions`, though Greenhouse's cover
 * letter is a fixed `#cover_letter` selector, not something this
 * function would ever surface as a custom question anyway).
 *
 * `role="combobox"` (a react-select dropdown -- confirmed live on the
 * "Are you eligible to work in the US?"/sponsorship-style questions)
 * is tagged `kind: "radio"`, the same human-only bucket Lever already
 * uses for binary-choice questions: it's not free text, and (like
 * Lever's own radio groups) disproportionately the sensitive-topic
 * questions, so it must never reach the LLM-answer-generation feature
 * regardless of ATS. E6 made this fail closed rather than a list of
 * known exceptions: `text` is only ever a textarea or a text-like input,
 * so a native <select>, a wrapper <div>/<fieldset>, or a date/number
 * input is `radio` too.
 */
export function extractCustomQuestions(doc: Document, excludeFieldName: string | null = null): CustomQuestion[] {
  const seen = new Set<string>();
  const questions: CustomQuestion[] = [];
  for (const element of doc.querySelectorAll<HTMLElement>("[id]")) {
    const id = element.id;
    if (!QUESTION_ID_PATTERN.test(id) || seen.has(id) || id === excludeFieldName) continue;
    if (element.closest(EXCLUDED_CONTAINER_SELECTOR) !== null) continue;
    if (element.tagName === "INPUT" && (element as HTMLInputElement).type === "hidden") continue;
    seen.add(id);
    const { label, sensitive } = labelForQuestion(doc, id);
    // D6, by label: `question_<id>` is the tenant's namespace, not
    // Greenhouse's -- real boards author "Gender" and "Pronouns" as
    // ordinary custom questions (Airbnb, Figma), outside both EEO
    // containers. See lib/questionSafety.ts.
    if (sensitive) continue;
    questions.push({ fieldName: id, label, kind: questionKind(element, label) });
  }
  return questions;
}

/**
 * The one path a value chosen off-page (a saved answer, an LLM draft)
 * ever reaches a real Greenhouse field -- mirrors the multi-layered
 * defense-in-depth `fillCustomTextAnswer` already established for Lever
 * (lib/lever.ts): re-validates the target itself rather than trusting
 * the caller already filtered correctly. Refuses anything outside the
 * `question_<numeric>` namespace, anything inside the EEO/demographic
 * containers, anything that isn't a genuine text-like input (never a
 * react-select combobox, file, radio, or checkbox), and uses
 * `setReactControlledValue` (not a plain assignment) since this is a
 * React-controlled form.
 *
 * `force` (E6 continuation) -- mirrors Lever's own `fillCustomTextAnswer`:
 * bypasses ONLY the D5 not-empty check at the bottom. Every refusal above
 * it (namespace, EEO container, control kind, D6 sensitive label) runs
 * unconditionally first and is never affected by `force`.
 */
export function fillCustomTextAnswer(
  doc: Document,
  fieldName: string,
  value: string,
  force = false,
): FillAnswerOutcome {
  if (!QUESTION_ID_PATTERN.test(fieldName)) return "refused";
  const element = doc.querySelector<HTMLElement>(`[id="${CSS.escape(fieldName)}"]`);
  if (element === null || element.id !== fieldName) return "refused";
  if (element.closest(EXCLUDED_CONTAINER_SELECTOR) !== null) return "refused";
  // A plain text-entry control only -- never a react-select combobox,
  // select, file, radio, checkbox, or a wrapper element.
  if (element.getAttribute("role") === "combobox" || !isTextEntryElement(element)) return "refused";
  // Same label rules as extraction (D6), re-run on the write path.
  const { label, sensitive } = labelForQuestion(doc, fieldName);
  if (label === null || sensitive) return "refused";
  // D5: never clobber text that's already there, unless the human
  // explicitly asked to replace it (`force`).
  if (hasExistingText(element) && !force) return "not_empty";

  setReactControlledValue(element, value);
  return "filled";
}
