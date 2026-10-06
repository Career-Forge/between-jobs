import type { StandardFieldSpec } from "./ats-field-map";
import { countryCandidateKeys, countryKey, countrySearchText } from "./countryNames";
import { isOrSitsInsideActivatable } from "./forbiddenControls";
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
    // `#country` (also live-confirmed present and often required) is NOT here: it is a
    // react-select combobox, not a plain text input -- setting text into its underlying
    // input only changes the search box, it doesn't commit a selected option, and a
    // text-entry fill here would leave a required field half-filled. It has its own path,
    // `fillCountryField` below, which picks an option from the list and checks the choice
    // took, or leaves the field alone and says why.
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


// ---- #country: a list, not a text box --------------------------------------------------

export type CountryOutcome =
  /** The form has no `#country`: nothing to fill, nothing to say. */
  | { status: "absent" }
  /** Already holds a choice and `forceRefillAll` was not asked: left as it is. */
  | { status: "left" }
  | { status: "filled" }
  /** The field is there and is still empty; `reason` says why. Shown to the person. */
  | { status: "not_filled"; reason: string };

export interface CountryTiming {
  /** How long to wait for the list to react to what was typed, and for the choice to show. */
  timeoutMs: number;
  pollMs: number;
}

const DEFAULT_COUNTRY_TIMING: CountryTiming = { timeoutMs: 800, pollMs: 25 };

async function waitUntil(condition: () => boolean, timing: CountryTiming): Promise<boolean> {
  const deadline = Date.now() + timing.timeoutMs;
  for (;;) {
    if (condition()) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, timing.pollMs));
  }
}

function isPlaceholderOption(option: HTMLOptionElement): boolean {
  return option.value === "" || /^(?:select|choose|please|pick)\b|^[-\u2013\u2014 ]+$/iu.test(option.text.trim());
}

function fillNativeCountrySelect(
  select: HTMLSelectElement,
  profileCountry: string,
  force: boolean,
): CountryOutcome {
  const options = Array.from(select.options);
  const selected = options[select.selectedIndex];
  // A native list always shows SOMETHING; only a placeholder means "not chosen yet".
  if (!force && selected !== undefined && !isPlaceholderOption(selected)) return { status: "left" };
  const candidates = new Set(countryCandidateKeys(profileCountry));
  const match = options.find(
    (option) => !isPlaceholderOption(option) && (candidates.has(countryKey(option.text)) || candidates.has(countryKey(option.value))),
  );
  if (match === undefined) {
    return { status: "not_filled", reason: "none of the form's country options matches the country in your profile" };
  }
  // A native <select> on a React form is still React-controlled: the prototype's own setter,
  // then the events React listens for.
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")?.set;
  if (setter !== undefined) setter.call(select, match.value);
  else select.value = match.value;
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  return select.value === match.value
    ? { status: "filled" }
    : { status: "not_filled", reason: "the form's country list did not take the choice -- check this field" };
}

// The react-select the board renders: an `input[role=combobox]` inside a control, whose parent
// holds both the control (with the chosen value, `...single-value`) and, while open, the menu
// with a `role=listbox` of `role=option` entries. Found through ARIA wherever the library
// exposes it, and through its class-name fragments only for the control and the shown value.
// (The input's own wrapper is also called a "container" -- `...input-container` -- and is too
// narrow to hold either, so the control's parent is the root.)
function comboboxRoot(input: HTMLElement): Element | null {
  return input.closest('[class*="control"]')?.parentElement ?? null;
}

function chosenText(input: HTMLElement): string | null {
  const shown = comboboxRoot(input)?.querySelector('[class*="single-value"]');
  const text = shown?.textContent?.trim();
  return text === undefined || text === "" ? null : text;
}

// The page decides its own markup, so nothing here trusts what `aria-controls` points at. The
// element it names is used only if it really is a listbox; otherwise the search is limited to
// the combobox's own widget.
function listboxOf(input: HTMLElement, doc: Document): Element | null {
  const listboxId = input.getAttribute("aria-controls");
  const byId = listboxId !== null && listboxId !== "" ? doc.getElementById(listboxId) : null;
  if (byId !== null && byId.getAttribute("role") === "listbox") return byId;
  return comboboxRoot(input)?.querySelector('[role="listbox"]') ?? null;
}

// What a list entry may be made of: plain layout elements, all the way from the option up to
// its listbox. An allow-list of tag names, not a list of bad ones, so an element nobody
// thought of is refused too.
const PLAIN_LIST_TAGS: ReadonlySet<string> = new Set(["DIV", "SPAN", "LI", "UL", "OL"]);

/**
 * True only for an entry of a listbox that is plain markup: every element from the option up to
 * its listbox is a plain layout element, and neither the option nor anything around it, up to
 * the document, is something a click would activate (a link, a button, a label forwarding to a
 * control, a form control, or an element with such a role). Job-page markup is untrusted, so a
 * page cannot steer the one click this engine makes onto a submit button, a link or a consent
 * box by calling it an "option".
 */
function isPlainListEntry(option: Element): boolean {
  let insideListbox = false;
  for (let el: Element | null = option; el !== null; el = el.parentElement) {
    if (!PLAIN_LIST_TAGS.has(el.tagName)) return false;
    if (el !== option && el.getAttribute("role") === "listbox") {
      insideListbox = true;
      break;
    }
  }
  return insideListbox && !isOrSitsInsideActivatable(option);
}

function listboxOptions(input: HTMLElement, doc: Document): HTMLElement[] {
  const listbox = listboxOf(input, doc);
  if (listbox === null) return [];
  return Array.from(listbox.querySelectorAll<HTMLElement>('[role="option"]')).filter(isPlainListEntry);
}

/** The one place the engine activates a list entry: a plain option of the country list it has
 * just filtered, inside a listbox. It re-checks that itself and does nothing otherwise, so it
 * can never click a button, a link, a label or anything else a page dresses up as an option. */
function activateListboxOption(option: HTMLElement): void {
  if (!isPlainListEntry(option)) return;
  option.click();
}

async function fillReactSelectCountry(
  input: HTMLInputElement,
  doc: Document,
  profileCountry: string,
  force: boolean,
  timing: CountryTiming,
): Promise<CountryOutcome> {
  if (!force && chosenText(input) !== null) return { status: "left" };
  const candidates = new Set(countryCandidateKeys(profileCountry));
  const unverified = "the form's country list did not respond as expected -- check this field";
  try {
    input.focus();
    setReactControlledValue(input, countrySearchText(profileCountry));
    const matching = () => listboxOptions(input, doc).find((o) => candidates.has(countryKey(o.textContent ?? "")));
    await waitUntil(() => matching() !== undefined, timing);
    const option = matching();
    if (option === undefined) {
      // A list that opened and offers no such country is a different answer from a list that
      // never reacted to the typing. Read before the search text is cleared (which closes it).
      const listOpened = listboxOf(input, doc) !== null;
      setReactControlledValue(input, ""); // don't leave a half-typed search in the box
      return {
        status: "not_filled",
        reason: listOpened ? "none of the form's country options matches the country in your profile" : unverified,
      };
    }
    activateListboxOption(option);
    const took = await waitUntil(() => {
      const shown = chosenText(input);
      return shown !== null && candidates.has(countryKey(shown));
    }, timing);
    return took ? { status: "filled" } : { status: "not_filled", reason: unverified };
  } catch {
    return { status: "not_filled", reason: unverified };
  }
}

/**
 * Fills Greenhouse's `#country` from the profile, when the control is one this code knows: a
 * plain `<select>`, or the standard react-select combobox. Anything else, a profile with no
 * country, a country no option matches, or a list that does not confirm the choice is
 * `not_filled` with a reason -- the field is left as it was, never half-filled. D5: a field
 * that already holds a choice is left alone unless `forceRefillAll`.
 *
 * Not verified against a live board: the react-select path depends on the list reacting to
 * typed text and to a click on an option, which a synthetic fixture can only imitate.
 */
export async function fillCountryField(
  doc: Document,
  profileCountry: string,
  forceRefillAll: boolean,
  timing: CountryTiming = DEFAULT_COUNTRY_TIMING,
): Promise<CountryOutcome> {
  const control = doc.getElementById("country");
  if (control === null) return { status: "absent" };
  if (profileCountry.trim() === "") return { status: "not_filled", reason: "your profile has no country" };
  if (control.tagName === "SELECT") {
    return fillNativeCountrySelect(control as HTMLSelectElement, profileCountry, forceRefillAll);
  }
  if (control.tagName === "INPUT" && control.getAttribute("role") === "combobox") {
    return fillReactSelectCountry(control as HTMLInputElement, doc, profileCountry, forceRefillAll, timing);
  }
  return {
    status: "not_filled",
    reason: "the form's country field is not a kind of list this extension knows how to fill",
  };
}
