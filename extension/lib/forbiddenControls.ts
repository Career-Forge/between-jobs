// The controls this extension never touches, whatever a field map or a page calls them.
// The human performs every irreversible step: submitting, moving to the next step, creating
// an account, signing in, entering a one-time code or PIN, answering a CAPTCHA, and ticking
// or accepting anything. The fill engine only ever writes text into a text-entry control,
// attaches a file to a file input, or picks a country in its own country list; it has no
// code path to any of the above and no source for a PIN or a code.
//
// What enforces that, and how strongly:
//  - The fill engine's own shape is the guarantee: it writes profile-derived text to
//    text-entry controls, and its only click is `activateListboxOption` in lib/greenhouse.ts,
//    which refuses anything that is not a plain entry of a listbox (see below).
//  - tests/neverSubmit.test.ts runs the real content script against pages full of such
//    controls with the DOM's click, submit, dispatch and focus entry points watched. That
//    behavioural run is the net for any spelling of a click or a tick.
//  - The same test also scans the engine's source. That scan is a tripwire for the ordinary
//    spellings of a click, a submit, a tick or a navigation, and for a literal that names such
//    a control. It is not a proof: obfuscated string-building is out of scope for static
//    analysis, and navigation is checked statically only.
//  - This file's vocabulary is a best-effort lock on a signed field map. A map whose selector
//    matches it is refused at load time, so it cannot even get as far as being asked to
//    write. It is deliberately over-inclusive -- a false positive costs a map that has to be
//    fixed and re-signed, a false negative would have the engine aim at a control it must not
//    touch -- but a deny-list is never complete (an opaque selector such as `#a1` passes it).
//    The second lock is that the engine writes only to text-entry controls, whatever a
//    selector resolves to.

const TYPE_ATTRIBUTE =
  /\btype\s*=\s*["']?(?:submit|button|reset|image|checkbox|radio|password)(?![a-z0-9])/iu;
// A role of `option` is deliberately absent: the country list the engine fills is a listbox
// of options, and picking one of them is the one activation it makes.
const ROLE_ATTRIBUTE = /\brole\s*=\s*["']?(?:button|link|checkbox|radio|switch|menuitem)(?![a-z0-9])/iu;
// A `type` or `role` matched by an attribute OPERATOR (`^=`, `$=`, `*=`, `~=`, `|=`), or a CSS
// escape (a backslash), can spell a forbidden value without writing it out; refused outright.
const OPERATOR_ATTRIBUTE = /(?:\btype|\brole)\s*[~|^$*]=|\\/iu;
const BUTTON_TAG = /(?:^|[\s>+~,(])(?:button|a)(?=$|[\s>+~,)[.#:])/iu;
// Boundaries are "not a letter or digit" rather than \b, so `verification_code`, `accept_terms`
// and `submit_app` are recognised and `nextSibling`, `mapping` and `disclosureConsent` are not.
// A bare `code` is not here (postal_code, country_code are ordinary fields), nor a bare `verify`
// (a `verify` literal names a function in the engine's own source).
const VOCABULARY =
  /(?<![a-z0-9])(?:submit|next|continue|create[ _-]?account|sign[ _-]?in|sign[ _-]?up|log[ _-]?in|login|captcha|recaptcha|hcaptcha|turnstile|otp|totp|hotp|mfa|2fa|two[ _-]?factor|one[ _-]?time|security[ _-]?code|auth(?:entication)?[ _-]?code|pin|passcode|password|passwd|verification|consent|agree|agreement|accept|terms|gdpr|privacy)(?![a-z0-9])/iu;

/** True when `selector` names a control the extension must never touch. */
export function selectorNamesForbiddenControl(selector: string): boolean {
  return (
    TYPE_ATTRIBUTE.test(selector) ||
    ROLE_ATTRIBUTE.test(selector) ||
    OPERATOR_ATTRIBUTE.test(selector) ||
    BUTTON_TAG.test(selector) ||
    VOCABULARY.test(selector)
  );
}

/** The vocabulary alone, for scanning text that is not a selector (the source scan). */
export function textNamesForbiddenControl(text: string): boolean {
  return TYPE_ATTRIBUTE.test(text) || ROLE_ATTRIBUTE.test(text) || VOCABULARY.test(text);
}

// Tag names and ARIA roles written as sets (not selector strings), so the source scan, which
// rejects a literal that names a control, has nothing to object to in the engine files that
// import the helper below.
const ACTIVATABLE_TAGS: ReadonlySet<string> = new Set([
  "A",
  "AREA",
  "BUTTON",
  "INPUT",
  "LABEL",
  "SELECT",
  "SUMMARY",
  "TEXTAREA",
]);
const ACTIVATABLE_ROLES: ReadonlySet<string> = new Set([
  "button",
  "link",
  "checkbox",
  "radio",
  "switch",
  "menuitem",
  "tab",
]);

/**
 * True when `element` is, or sits inside, something a click would activate: a link, a button,
 * a label (which forwards the click to its control), a form control, or an element whose ARIA
 * role says it acts like one. A page decides its own markup, so this is how the one click the
 * engine makes refuses to land on anything but a plain list entry.
 */
export function isOrSitsInsideActivatable(element: Element): boolean {
  for (let el: Element | null = element; el !== null; el = el.parentElement) {
    if (ACTIVATABLE_TAGS.has(el.tagName.toUpperCase())) return true;
    const role = el.getAttribute("role")?.trim().toLowerCase();
    if (role !== undefined && ACTIVATABLE_ROLES.has(role)) return true;
  }
  return false;
}
