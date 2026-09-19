import type { StandardFieldSpec } from "./ats-field-map";
import { isTextEntryElement } from "./questionSafety";
import type { ExtensionPersonalInfo } from "./types";

// E4/E5 -- pulled out of lib/lever.ts, which used to own this logic
// entirely. Everything in this file is genuinely ATS-agnostic: it only
// ever reads `ExtensionPersonalInfo` and a caller-supplied
// `StandardFieldSpec[]`, never anything Lever/Greenhouse/Ashby-specific.
// lib/lever.ts re-exports these names so E2/E3's existing call sites and
// tests (which import them from "@/lib/lever") keep working unchanged.

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

// E4 -- Greenhouse splits a person's name into separate `first_name`/
// `last_name` inputs; `ExtensionPersonalInfo.name` (confirmed against the
// real GET /applications/{id}/extension-payload contract) is one full-name
// string, matching how Lever's own single `name` field already worked.
// There's no structured first/last-name split anywhere in the profile
// schema to draw on instead, so this is a best-effort heuristic, not a
// claim of real name-parsing: the first whitespace-separated token is
// treated as the given name, everything after it (rejoined) as the
// family name. Good enough for a D5 idempotent fill the person can
// correct by hand; a real name-parsing library would be overkill for a
// single non-identity-critical form field.
function splitName(fullName: string): { first: string; rest: string } {
  const trimmed = fullName.trim();
  const spaceIndex = trimmed.indexOf(" ");
  if (spaceIndex === -1) return { first: trimmed, rest: "" };
  return { first: trimmed.slice(0, spaceIndex), rest: trimmed.slice(spaceIndex + 1).trim() };
}

/**
 * The declarative interpreter E3c introduced in place of `STANDARD_
 * FIELDS`' old `getValue` closures -- functions can't be part of a
 * signed JSON payload, so this reproduces a pure, generic vocabulary a
 * signed map (or a GENERIC_FIELD_DEFAULTS) can express as plain data:
 * `direct` (a plain field read, empty string treated as "no value"),
 * `fallback` (first non-empty of several), `joinNonEmpty` (filters
 * empties, joins the rest), and E4's `firstNameWord`/`lastNameWord`
 * (splits the first non-empty of several full-name fields per
 * `splitName` above -- added for Greenhouse's separate first/last-name
 * inputs, harmless no-ops for any ATS that only ever sends "name").
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
    case "firstNameWord": {
      for (const path of spec.profileFields) {
        const value = getProfileValue(info, path);
        if (value) return splitName(value).first || null;
      }
      return null;
    }
    case "lastNameWord": {
      for (const path of spec.profileFields) {
        const value = getProfileValue(info, path);
        if (value) return splitName(value).rest || null;
      }
      return null;
    }
  }
}

export interface StandardFieldPlanResult {
  plan: FieldFillPlanItem[];
  /** Selectors from the supplied specs that the browser refused to parse.
   * A valid signature proves a signed map is AUTHENTIC, not that every
   * selector in it is well-formed -- a maintainer typo must cost that one
   * field, not the whole fill. */
  invalidSelectors: string[];
}

/**
 * D5 (browser-extension.md) -- Fill is a repeatable, idempotent action
 * against a fresh DOM read: a field already holding a non-empty value
 * (whether the user typed it or a prior Fill set it) is skipped unless
 * `forceRefillAll` is passed. Pure with respect to the DOM (only reads
 * `.value`) -- the actual mutation happens in `applyFillPlan`/
 * `applyReactControlledFillPlan` below, so this planning step is shared
 * verbatim by every ATS regardless of how its inputs need to be written.
 *
 * Only plain text-entry controls are ever planned (a map that points at a
 * select, checkbox or submit button gets skipped, even under
 * `forceRefillAll`): the signed map is the only thing deciding these
 * selectors, so this is the second lock on the door, not the first.
 */
export function planStandardFieldFillsChecked(
  doc: Document,
  personalInfo: ExtensionPersonalInfo,
  forceRefillAll: boolean,
  standardFields: readonly StandardFieldSpec[],
): StandardFieldPlanResult {
  const plan: FieldFillPlanItem[] = [];
  const invalidSelectors: string[] = [];
  for (const field of standardFields) {
    let element: Element | null;
    try {
      element = doc.querySelector(field.selector);
    } catch {
      invalidSelectors.push(field.selector);
      continue;
    }
    if (element === null || !isTextEntryElement(element)) continue;
    if (!forceRefillAll && element.value.trim() !== "") continue;
    const value = resolveFieldValue(field, personalInfo);
    if (value === null || value === "") continue;
    plan.push({ selector: field.selector, value });
  }
  return { plan, invalidSelectors };
}

export function planStandardFieldFills(
  doc: Document,
  personalInfo: ExtensionPersonalInfo,
  forceRefillAll: boolean,
  standardFields: readonly StandardFieldSpec[],
): FieldFillPlanItem[] {
  return planStandardFieldFillsChecked(doc, personalInfo, forceRefillAll, standardFields).plan;
}

/** The element a plan item targets, or null when it no longer exists, its
 * selector doesn't parse, or it isn't a plain text-entry control. */
function planTarget(doc: Document, item: FieldFillPlanItem): HTMLInputElement | HTMLTextAreaElement | null {
  let element: Element | null;
  try {
    element = doc.querySelector(item.selector);
  } catch {
    return null;
  }
  return element !== null && isTextEntryElement(element) ? element : null;
}

/**
 * Lever's own fill mechanism (E2) -- Lever's apply form is server-rendered
 * HTML, so a plain `.value` assignment plus a dispatched `input`/`change`
 * event is sufficient; nothing here needs to fight a framework's own
 * value tracking. NOT safe to reuse for a React-controlled form (see
 * `applyReactControlledFillPlan` below) -- kept as its own function,
 * rather than a shared one with a strategy flag, so a future reader can
 * tell at a glance which ATSs need which mechanism.
 */
export function applyFillPlan(doc: Document, plan: readonly FieldFillPlanItem[]): string[] {
  const filled: string[] = [];
  for (const item of plan) {
    const element = planTarget(doc, item);
    if (element === null) continue;
    element.value = item.value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    filled.push(item.selector);
  }
  return filled;
}

/**
 * E4/E5 -- the one genuinely new DOM-interaction technique this phase
 * introduces, confirmed live (not assumed from memory) against a real
 * Melio/Cloudflare Greenhouse posting and a real Ashby (Foundry for
 * Good) posting: both `job-boards.greenhouse.io` and `jobs.ashbyhq.com`
 * render their apply forms as React-CONTROLLED inputs. React installs
 * its own instance-level get/set descriptor over the DOM node's native
 * `value` property for change-tracking purposes; a plain
 * `element.value = x` invokes THAT wrapped setter (confirmed live via
 * each field's own `__reactProps$...`/fiber -- the DOM's `.value`
 * visibly updated to the new text, but React's own internal prop stayed
 * the OLD value), so the framework never learns the field changed and
 * silently reverts it on the next render (e.g. the very next keystroke
 * elsewhere in the form, or at Submit time, since React -- not the DOM --
 * is the actual source of truth for what gets submitted). Confirmed live
 * that Lever's form has no such descriptor at all, which is exactly why
 * `applyFillPlan` above has never needed this.
 *
 * The fix (also confirmed live, on the same real fields, via the same
 * fiber inspection): invoke the *native*, prototype-level setter
 * directly -- bypassing the instance-level descriptor React installed --
 * before dispatching a real `input` event. Because the DOM's actual
 * value changed via a path React's own tracker didn't observe, React's
 * event-delegation handler correctly sees a genuine change on the next
 * native `input` event and updates its internal state to match. This is
 * the standard, widely-documented workaround for scripted testing
 * against React-controlled inputs (the same technique the reference repo
 * named in browser-extension.md, berellevy/job_app_filler, and testing
 * libraries like Test Library/Cypress all rely on) -- verified here
 * against this project's own real target pages rather than assumed from
 * that prior art.
 */
export function setReactControlledValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const prototype = element.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const nativeValueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  if (nativeValueSetter) {
    nativeValueSetter.call(element, value);
  } else {
    // No such descriptor exists (e.g. a non-browser test environment) --
    // fall back to a plain assignment rather than throwing, matching
    // this function's "best effort, never crash the fill" contract.
    element.value = value;
  }
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

/** Greenhouse/Ashby's counterpart to `applyFillPlan`, using
 * `setReactControlledValue` instead of a plain assignment. */
export function applyReactControlledFillPlan(doc: Document, plan: readonly FieldFillPlanItem[]): string[] {
  const filled: string[] = [];
  for (const item of plan) {
    const element = planTarget(doc, item);
    if (element === null) continue;
    setReactControlledValue(element, item.value);
    filled.push(item.selector);
  }
  return filled;
}

/**
 * The only working approach for `<input type="file">` -- browsers block
 * scripts from setting `.value` on a file input directly. Constructs a
 * real `File` from bytes already fetched, wraps it in a `DataTransfer`,
 * and dispatches a `change` event the page's own upload-handling JS
 * picks up exactly as if the user had picked a file. Confirmed live this
 * works identically on all three ATSs' file inputs (Lever's E2/E3a,
 * Greenhouse's and Ashby's own resume/cover-letter uploads here) --
 * unlike `value`, `.files` isn't something React can silently
 * "control" out from under a script the way it does text input `value`,
 * since React's own change-tracking wrapper is specific to
 * `value`/`checked`, not `files`, and a genuine `change` event's
 * `target.files` is read directly off the DOM at dispatch time.
 */
export function attachFile(input: HTMLInputElement, bytes: ArrayBuffer, filename: string, mimeType: string): void {
  // Assigning `.files` on anything but a file input is meaningless at best;
  // a selector that resolves to some other element (a same-named text
  // input, say) must never receive a synthetic change event as if a file
  // had been chosen.
  if (input.tagName !== "INPUT" || input.type !== "file") {
    throw new Error("Refusing to attach a file to an element that isn't a file input.");
  }
  const file = new File([bytes], filename, { type: mimeType });
  const dataTransfer = new DataTransfer();
  dataTransfer.items.add(file);
  input.files = dataTransfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}
