import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { KNOWN_PUBLIC_KEYS, verifyAndParseFieldMap } from "@/lib/ats-field-map";
import type { SignedFieldMapResponse } from "@/lib/ats-field-map";
import * as ashby from "@/lib/ashby";
import { isOrSitsInsideActivatable, selectorNamesForbiddenControl } from "@/lib/forbiddenControls";
import * as greenhouse from "@/lib/greenhouse";
import * as lever from "@/lib/lever";
import { LEVER_MAP, loadContentScript, PERSON, trackedState } from "./helpers/contentHarness";
import { scanSource, watchInteractions, type Watcher } from "./helpers/neverSubmit";

// The never-submit guard. The human performs every irreversible step: this extension
// never submits, never clicks Next or Continue, never creates an account or signs in, never
// types a PIN or a verification code, never touches a CAPTCHA, never ticks or accepts anything.
//
// Three nets, each shown to fail on a plant:
//   1. a static scan of the source of the field-map loader, the default maps and the fill
//      engine (comments excluded -- it reads the syntax tree);
//   2. a behavioural run of the real content script against synthetic pages full of such
//      controls, with the DOM's click/submit/dispatch/focus entry points watched;
//   3. the signed-map loader refusing a map that aims at one.

const here = (relative: string) => fileURLToPath(new URL(relative, import.meta.url));
const read = (relative: string) => readFileSync(here(relative), "utf8");

// ---- 1. the static scan ----------------------------------------------------------------------

// Everything in lib/ and the content script is scanned, except the files below, each for a
// stated reason. The list is itself checked: a file that stops existing, or a new one that
// is not scanned, fails a test.
const NOT_SCANNED: Record<string, string> = {
  "forbiddenControls.ts": "defines the vocabulary, so its own patterns name the controls (checked separately for calls)",
  "consent.ts": "the stored-agreement flag: its refusal message is about consent, and it touches no page",
  "api.ts": "network calls, no page",
  "supabase.ts": "session storage, no page",
  "types.ts": "type declarations only",
};
const ENGINE_FILES = [
  ...readdirSync(here("../lib"))
    .filter((name) => name.endsWith(".ts") && !(name in NOT_SCANNED))
    .map((name) => `../lib/${name}`),
  "../entrypoints/content.ts",
];

// The one click the engine makes: activating an option of the country list it has just
// filtered (lib/greenhouse.ts). The one focus: that list's own input.
const ALLOW = { allowClickIn: ["activateListboxOption"], allowFocusIn: ["fillReactSelectCountry"] };
// "consent_required" is a status the content script reports (nothing was read because the
// person has not agreed); it names no control on any page.
const STATUS_LITERALS = { allowLiterals: ["consent_required"] };
const optionsFor = (file: string) => ({
  ...(file.endsWith("lib/greenhouse.ts") ? ALLOW : {}),
  ...(file.endsWith("entrypoints/content.ts") ? STATUS_LITERALS : {}),
});

describe("static scan of the field maps, the signed-map loader and the fill engine", () => {
  it("excuses only files that exist, each for a stated reason", () => {
    const present = new Set(readdirSync(here("../lib")));
    for (const [name, reason] of Object.entries(NOT_SCANNED)) {
      expect(present.has(name), name).toBe(true);
      expect(reason.length).toBeGreaterThan(10);
    }
  });

  it("covers the files that matter", () => {
    for (const must of ["lever.ts", "greenhouse.ts", "ashby.ts", "standardFields.ts", "ats-field-map.ts"]) {
      expect(ENGINE_FILES).toContain(`../lib/${must}`);
    }
    expect(ENGINE_FILES).toContain("../entrypoints/content.ts");
  });

  it.each(ENGINE_FILES)("%s has no submit/next/sign-in/consent selector and no click, submit, keyboard event or navigation", (file) => {
    expect(scanSource(file, read(file), optionsFor(file))).toEqual([]);
  });

  it("the vocabulary module itself makes no click, submit, dispatch or navigation (only its patterns name the controls)", () => {
    const violations = scanSource("lib/forbiddenControls.ts", read("../lib/forbiddenControls.ts")).filter(
      (v) => v.rule !== "literal-names-a-forbidden-control",
    );
    expect(violations).toEqual([]);
  });

  it("the only click in the whole engine is the one in activateListboxOption, in lib/greenhouse.ts", () => {
    const withoutAllowance = ENGINE_FILES.flatMap((file) =>
      scanSource(file, read(file), file.endsWith("entrypoints/content.ts") ? STATUS_LITERALS : {}),
    );
    expect(withoutAllowance.map((v) => `${v.file.split("/").pop()}:${v.rule}`).sort()).toEqual([
      "greenhouse.ts:click",
      "greenhouse.ts:focus",
    ]);
  });
});

describe("the static scan fails on a planted violation", () => {
  const PLANTS: Array<[string, string, string]> = [
    ["a submit-button selector", `const b = document.querySelector('button[type="submit"]');`, "literal-names-a-forbidden-control"],
    ["a submit-input selector", `const SELECTOR = "input[type=submit]";`, "literal-names-a-forbidden-control"],
    ["a Next button by its label", `const NEXT = "Next";`, "literal-names-a-forbidden-control"],
    ["a Continue button", "const c = `button:contains(${'Continue'})`;", "literal-names-a-forbidden-control"],
    ["a Create account control", `const x = "#create-account";`, "literal-names-a-forbidden-control"],
    ["a Sign in control", `const x = ".sign-in";`, "literal-names-a-forbidden-control"],
    ["a Log in control", `const x = "a.log-in";`, "literal-names-a-forbidden-control"],
    ["a PIN field", `const x = 'input[name="pin"]';`, "literal-names-a-forbidden-control"],
    ["a verification-code field", `const x = "#verification_code";`, "literal-names-a-forbidden-control"],
    ["a CAPTCHA", `const x = "textarea.g-recaptcha-response";`, "literal-names-a-forbidden-control"],
    ["a consent checkbox", `const x = 'input[name="consent"]';`, "literal-names-a-forbidden-control"],
    ["an agree box", `const x = "#agree";`, "literal-names-a-forbidden-control"],
    ["a checkbox by type", `const x = 'input[type="checkbox"]';`, "literal-names-a-forbidden-control"],
    ["a role=button", `const x = '[role="button"]';`, "literal-names-a-forbidden-control"],
    ["a regex that names a control", `const rx = /submit/i;`, "literal-names-a-forbidden-control"],
    ["a click", `function go(button: HTMLElement) { button.click(); }`, "click"],
    ["a click through a chain", `document.querySelector("#a")!.click();`, "click"],
    ["requestSubmit", `form.requestSubmit();`, "submit"],
    ["form.submit()", `form.submit();`, "submit"],
    ["a reference to submit without calling it", `const f = form.submit;`, "submit"],
    ["an Enter keydown", `el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));`, "dispatchEvent-of-anything-but-input-or-change"],
    ["a synthetic mouse event", `const e = new MouseEvent("click");`, "synthetic-keyboard-or-mouse-event"],
    ["a submit event", `el.dispatchEvent(new Event("submit"));`, "dispatchEvent-of-anything-but-input-or-change"],
    ["an event whose type is not a literal", `el.dispatchEvent(new Event(kind));`, "dispatchEvent-of-anything-but-input-or-change"],
    ["a prebuilt event", `el.dispatchEvent(event);`, "dispatchEvent-of-anything-but-input-or-change"],
    ["ticking a box", `box.checked = true;`, "ticks-a-box"],
    ["navigating", `location.href = "https://example.com/next";`, "navigation"],
    ["window.open", `window.open("https://example.com");`, "navigation"],
    ["location.assign", `location.assign("/apply/step-2");`, "navigation"],
    ["a focus outside the country list", `function fillName(i: HTMLElement) { i.focus(); }`, "focus"],
    // The ordinary evasions of the plain spellings above.
    ["a click reached through .call", `b.click.call(b);`, "click"],
    ["a click through the prototype", `HTMLElement.prototype.click.call(b);`, "click"],
    ["a click through Reflect", `Reflect.apply(b.click, b, []);`, "indirect-call"],
    ["a destructured click", `const { click } = b; click();`, "destructures-a-dangerous-member"],
    ["a renamed destructured click", `const { click: go } = b;`, "destructures-a-dangerous-member"],
    ["a click by element access", `b["click"]();`, "element-access-to-a-dangerous-member"],
    ["a submit by element access", `form["requestSubmit"]();`, "element-access-to-a-dangerous-member"],
    ["a name built with String.fromCharCode", `const m = String.fromCharCode(99, 108, 105, 99, 107);`, "indirect-call"],
    ["code built at run time", `const f = new Function("b", "b.x()");`, "indirect-call"],
    ["assigning window.location", `window.location = "https://example.com/step2";`, "navigation"],
    ["assigning document.location", `document.location = "https://example.com/step2";`, "navigation"],
    ["assigning a bare location", `location = "https://example.com/step2";`, "navigation"],
    ["assigning location.hash", `location.hash = "#next";`, "navigation"],
    ["navigating with ||=", `link.href ||= "/next";`, "navigation"],
    ["ticking with ||=", `b.checked ||= true;`, "ticks-a-box"],
    ["ticking with ??=", `b.checked ??= true;`, "ticks-a-box"],
    ["ticking by element access", `b["checked"] = true;`, "ticks-a-box"],
    ["ticking with setAttribute", `b.setAttribute("checked", "");`, "ticks-a-box"],
    ["ticking with toggleAttribute", `b.toggleAttribute("checked");`, "ticks-a-box"],
    ["ticking with defineProperty", `Object.defineProperty(b, "checked", { value: true });`, "ticks-a-box"],
    ["ticking with Object.assign", `Object.assign(b, { checked: true });`, "ticks-a-box"],
  ];

  it.each(PLANTS)("%s", (_name, code, rule) => {
    const rules = scanSource("planted.ts", code).map((v) => v.rule);
    expect(rules).toContain(rule);
  });

  it("an ordinary read of location, and a variable called checked, are not code that moves or ticks", () => {
    const code = `const here = location.href; const host = window.location.hostname; let checked = false; checked = !checked;`;
    expect(scanSource("reads.ts", code)).toEqual([]);
  });

  it("a plain input/change event is fine", () => {
    expect(scanSource("ok.ts", `el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change"));`)).toEqual([]);
  });

  it("words in comments are not code", () => {
    const code = `// never clicks Submit, Next, Continue, Sign in or ticks a consent box\n/* button.click(); form.submit(); */\nconst ok = 1;`;
    expect(scanSource("comments.ts", code)).toEqual([]);
  });

  it("a click is allowed only inside the named function, and only where the caller allows it", () => {
    const code = `function activateListboxOption(o: HTMLElement) { o.click(); }\nfunction other(o: HTMLElement) { o.click(); }`;
    expect(scanSource("x.ts", code, { allowClickIn: ["activateListboxOption"] })).toHaveLength(1);
    expect(scanSource("x.ts", code)).toHaveLength(2);
  });

  it("planting a selector into the real fill engine's source is caught", () => {
    for (const file of ["../lib/lever.ts", "../lib/greenhouse.ts", "../lib/ashby.ts", "../lib/standardFields.ts", "../entrypoints/content.ts"]) {
      const planted = `${read(file)}\nexport const PLANTED = 'button[type="submit"]';\n`;
      const violations = scanSource(file, planted, optionsFor(file));
      expect(violations.map((v) => v.rule), file).toEqual(["literal-names-a-forbidden-control"]);
    }
  });

  it("planting a click into the real fill engine's source is caught", () => {
    const planted = `${read("../lib/standardFields.ts")}\nexport function oops(b: HTMLElement) { b.click(); }\n`;
    expect(scanSource("../lib/standardFields.ts", planted).map((v) => v.rule)).toEqual(["click"]);
  });
});

// ---- 3. the vocabulary, the default maps and the signed-map loader ----------------------------

describe("the default maps name no forbidden control", () => {
  const defaults = {
    lever: lever.GENERIC_FIELD_DEFAULTS,
    greenhouse: greenhouse.GENERIC_FIELD_DEFAULTS,
    ashby: ashby.GENERIC_FIELD_DEFAULTS,
  };
  for (const [name, map] of Object.entries(defaults)) {
    it(`${name}: every selector is an ordinary field`, () => {
      const selectors = [
        map.detectionSelector,
        map.resumeSelector,
        ...("coverLetterSelector" in map ? [map.coverLetterSelector] : []),
        ...map.standardFields.map((f) => f.selector),
      ];
      expect(selectors.length).toBeGreaterThan(2);
      for (const selector of selectors) expect(selectorNamesForbiddenControl(selector), selector).toBe(false);
    });
  }
});

describe("selectorNamesForbiddenControl", () => {
  const FORBIDDEN = [
    'button[type="submit"]',
    "input[type=submit]",
    'input[type="button"]',
    'input[type="checkbox"]',
    'input[type="radio"]',
    'input[type="password"]',
    "button",
    "div > button.primary",
    "a.apply",
    '[role="button"]',
    "#submit",
    "#submit_app",
    ".next-step",
    'button:has-text("Continue")',
    "#create-account",
    "a.sign-in",
    '[data-qa="log-in"]',
    "#login",
    'input[name="pin"]',
    'input[name="verification_code"]',
    'input[name="otp"]',
    "textarea.g-recaptcha-response",
    ".h-captcha",
    '#consent, input[name="agree"]',
    'input[name="accept_terms"]',
    // Words that were only ever covered by another word in the same selector.
    "#hcaptcha",
    ".cf-turnstile",
    'input[name="passcode"]',
    "#accept",
    "#terms",
    "#sign-up",
    '[role="switch"]',
    "div[role=switch]",
    // One-time codes, agreements and passwords, by their usual attribute values.
    'input[name="totp"]',
    'input[name="hotp"]',
    'input[name="two_factor"]',
    'input[name="two-factor-code"]',
    'input[type="reset"]',
    'input[type="image"]',
    '[role="link"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="menuitem"]',
    'input[name="mfa"]',
    'input[name="2fa"]',
    '[autocomplete="one-time-code"]',
    'input[name="security_code"]',
    'input[name="authentication_code"]',
    '[name="agreement"]',
    '[name="gdpr"]',
    '[name="privacy"]',
    '[name="password"]',
    '[name="passwd"]',
    // A type or role spelled by an attribute operator or a CSS escape.
    'input[type^="sub"]',
    '[type$="mit"]',
    '[type*="ubmit"]',
    'input[type="su\\62mit"]',
    '[role~="button"]',
    '[role^="but"]',
    '[role|="button"]',
  ];
  const ALLOWED = [
    'input[name="name"]',
    'input[name="email"]',
    'input[name="urls[LinkedIn]"]',
    'input[name="urls[Other (portfolio, GitHub etc)]"]',
    "#first_name",
    "#_systemfield_resume",
    'input[type="file"]',
    'input[type="tel"]',
    'input[name="location"]',
    ".application-field",
    '[data-field-path="_systemfield_name"]',
    // A bare `code` is an ordinary field name.
    'input[name="postal_code"]',
    'input[name="country_code"]',
    'input[name="zip_code"]',
    'input[type="text"]',
    'input[type="url"]',
  ];
  it.each(FORBIDDEN)("refuses %s", (selector) => expect(selectorNamesForbiddenControl(selector)).toBe(true));
  it.each(ALLOWED)("allows %s", (selector) => expect(selectorNamesForbiddenControl(selector)).toBe(false));
});

describe("isOrSitsInsideActivatable: what a click would activate, by tag and by role", () => {
  const make = (tag: string, role?: string): HTMLElement => {
    const el = document.createElement(tag);
    if (role !== undefined) el.setAttribute("role", role);
    return el;
  };

  it.each(["a", "area", "button", "input", "label", "select", "summary", "textarea"])("a <%s> is", (tag) => {
    expect(isOrSitsInsideActivatable(make(tag))).toBe(true);
  });

  it.each(["button", "link", "checkbox", "radio", "switch", "menuitem", "tab", "BUTTON", " Link "])(
    "an element with role %j is",
    (role) => {
      expect(isOrSitsInsideActivatable(make("div", role))).toBe(true);
    },
  );

  it("a plain element inside one is too, however deep, and one beside it is not", () => {
    const wrapper = make("label");
    const middle = make("div");
    const leaf = make("span", "option");
    wrapper.append(middle);
    middle.append(leaf);
    expect(isOrSitsInsideActivatable(leaf)).toBe(true);
    const lone = make("div");
    lone.append(make("span", "option"));
    expect(isOrSitsInsideActivatable(lone.firstElementChild!)).toBe(false);
  });

  it.each(["div", "span", "li", "ul", "ol", "section", "p"])("a plain <%s> is not", (tag) => {
    expect(isOrSitsInsideActivatable(make(tag))).toBe(false);
  });

  it.each(["option", "listbox", "presentation", "group"])("an element with role %j is not", (role) => {
    expect(isOrSitsInsideActivatable(make("div", role))).toBe(false);
  });
});

describe("the signed-map loader refuses a map that aims at a forbidden control", () => {
  const KEY_ID = "never-submit-test-key";
  let privateKey: CryptoKey;
  const b64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes));

  beforeAll(async () => {
    const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    privateKey = pair.privateKey;
    KNOWN_PUBLIC_KEYS[KEY_ID] = b64(new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey)));
  });

  async function signed(atsType: "lever" | "greenhouse" | "ashby", selector: string): Promise<SignedFieldMapResponse> {
    const payload: Record<string, unknown> = {
      ats_type: atsType,
      version: 1,
      schema: "ats-field-map/v1",
      standard_fields: [{ field: "x", selector, strategy: "direct", profileFields: ["linkedin"] }],
    };
    if (atsType === "lever") {
      Object.assign(payload, {
        custom_question_prefix: "cards[",
        label_wrapper_selector: ".application-field",
        label_selector: ".application-label",
        cover_letter_label_pattern: "cover letter",
      });
    }
    const canonical = JSON.stringify(payload);
    const signature = await crypto.subtle.sign({ name: "Ed25519" }, privateKey, new TextEncoder().encode(canonical));
    return {
      ats_type: atsType,
      version: 1,
      schema: "ats-field-map/v1",
      payload_canonical: canonical,
      signature_b64: b64(new Uint8Array(signature)),
      signing_key_id: KEY_ID,
    };
  }

  const PLANTED = [
    'button[type="submit"]',
    "#next",
    "a.continue",
    "#create-account",
    ".sign-in",
    "#login",
    'input[name="pin"]',
    "#verification_code",
    "textarea.g-recaptcha-response",
    'input[name="consent"]',
    '[role="button"]',
    "#sign-up",
    '[role="switch"]',
    'input[name="totp"]',
    '[autocomplete="one-time-code"]',
    '[name="agreement"]',
    'input[type^="sub"]',
    '[role~="button"]',
  ];

  for (const atsType of ["lever", "greenhouse", "ashby"] as const) {
    it.each(PLANTED)(`${atsType}: a validly signed map carrying %s is refused`, async (selector) => {
      expect(await verifyAndParseFieldMap(await signed(atsType, selector))).toBeNull();
    });

    it(`${atsType}: the same map with an ordinary selector is accepted, so the refusal is the selector's doing`, async () => {
      expect(await verifyAndParseFieldMap(await signed(atsType, 'input[name="urls[LinkedIn]"]'))).not.toBeNull();
    });
  }
});

// ---- 2. behaviour ---------------------------------------------------------------------------

const TRAPS = `
  <button type="submit">Submit application</button>
  <button type="button">Next</button>
  <button>Continue</button>
  <button>Create account</button>
  <a href="#signin" role="button">Sign in</a>
  <button>Log in</button>
  <input type="submit" value="Apply" />
  <input type="text" name="pin" placeholder="PIN" />
  <input type="text" name="verification_code" autocomplete="one-time-code" />
  <textarea name="g-recaptcha-response"></textarea>
  <div class="g-recaptcha" data-sitekey="synthetic"></div>
  <input type="checkbox" id="consent" name="consent" /><label for="consent">I agree to the terms</label>
  <input type="checkbox" id="privacy" name="privacy_policy" />
`;

const EVENTS = ["click", "dblclick", "submit", "keydown", "keyup", "keypress", "mousedown", "mouseup", "input", "change", "focus", "focusin"];

/** Listeners on every trap and on the form: any event that reaches one is recorded. */
function listenOnTraps(hits: string[]): void {
  const targets = document.querySelectorAll<HTMLElement>(
    'button, a, input[type="submit"], input[name="pin"], input[name="verification_code"], textarea[name="g-recaptcha-response"], .g-recaptcha, input[name="consent"], input[name="privacy_policy"], form',
  );
  for (const el of targets) {
    // An ancestor hears the events of the fields inside it (an option of the country list
    // being clicked, a field's input/change), which is not the form being touched; a form
    // only counts for what would act on it: being submitted, or Enter pressed in it.
    const types = el.tagName === "FORM" ? ["submit", "keydown", "keyup", "keypress"] : EVENTS;
    for (const type of types) {
      el.addEventListener(type, () => hits.push(`${type} on ${el.tagName.toLowerCase()}${el.getAttribute("name") ? `[${el.getAttribute("name")}]` : ""}`));
    }
  }
}

function expectTrapsUntouched(): void {
  expect(document.querySelector<HTMLInputElement>('input[name="pin"]')!.value).toBe("");
  expect(document.querySelector<HTMLInputElement>('input[name="verification_code"]')!.value).toBe("");
  expect(document.querySelector<HTMLTextAreaElement>('textarea[name="g-recaptcha-response"]')!.value).toBe("");
  expect(document.querySelector<HTMLInputElement>("#consent")!.checked).toBe(false);
  expect(document.querySelector<HTMLInputElement>("#privacy")!.checked).toBe(false);
}

const noFiles = (input: HTMLInputElement) => Object.defineProperty(input, "files", { value: undefined, writable: true, configurable: true });
const PDF = (name: string) => ({ base64: btoa("%PDF-1.4 synthetic"), filename: name });

describe("the real content script, on pages full of buttons it must never press", () => {
  let watcher: Watcher | null = null;
  afterEach(() => {
    watcher?.restore();
    watcher = null;
    document.body.innerHTML = "";
  });

  async function drive(
    href: string,
    state: ReturnType<typeof trackedState>,
    question: string | null,
  ): Promise<{ hits: string[]; watcher: Watcher; send: (m: Parameters<Awaited<ReturnType<typeof loadContentScript>>["send"]>[0]) => Promise<unknown> }> {
    const hits: string[] = [];
    listenOnTraps(hits);
    const harness = await loadContentScript({
      href,
      respond: (m) => (m.type === "PAGE_DETECTED" ? state : m.type === "VERIFY_SESSION" ? { valid: true } : undefined),
    });
    watcher = watchInteractions();
    await harness.send({ type: "GET_DETECTION_STATE" });
    await harness.send({ type: "REQUEST_FILL", forceRefillAll: false });
    await harness.send({ type: "REQUEST_FILL", forceRefillAll: true });
    await harness.send({ type: "RECHECK" });
    if (question !== null) {
      await harness.send({ type: "FILL_FIELD", fieldName: question, value: "A drafted answer", force: true });
    }
    return { hits, watcher, send: harness.send };
  }

  const withFiles = (extra: Partial<ReturnType<typeof trackedState>> = {}) =>
    trackedState({
      payload: { prepare_result: { resume: { artifact_id: "a", version_id: "v" }, cover_letter: { artifact_id: "b", version_id: "w" } }, personal_info: PERSON },
      resume: PDF("resume.pdf"),
      coverLetter: PDF("cover-letter.pdf"),
      ...extra,
    });

  it("Lever (with a signed map): fills its fields, presses nothing", async () => {
    document.body.innerHTML = `
      <form id="apply">
        <input type="file" name="resume" /><input type="text" name="name" /><input type="email" name="email" />
        <input type="text" name="phone" /><input type="text" name="location" />
        <div><div class="application-label">Cover letter</div><div class="application-field"><input type="file" name="cards[cl][field0]" /></div></div>
        <div><div class="application-label">Why us?</div><div class="application-field"><textarea name="cards[q1][field0]"></textarea></div></div>
        ${TRAPS}
      </form>
      ${TRAPS}`;
    for (const input of document.querySelectorAll<HTMLInputElement>('input[type="file"]')) noFiles(input);

    const { hits, watcher: w } = await drive(
      "https://jobs.lever.co/acme/aaaa-1111/apply",
      withFiles({ fieldMap: LEVER_MAP }),
      "cards[q1][field0]",
    );

    expect(document.querySelector<HTMLInputElement>('input[name="name"]')!.value).toBe("Alice Example"); // it did real work
    expect(document.querySelector<HTMLTextAreaElement>('textarea[name="cards[q1][field0]"]')!.value).toBe("A drafted answer");
    expect(w.interactions.some((i) => i.kind === "dispatch")).toBe(true); // and the watcher was live
    expect(w.violations()).toEqual([]);
    expect(hits).toEqual([]);
    expectTrapsUntouched();
  });

  it("Greenhouse (with the country list): picks a country option, presses nothing else", async () => {
    document.body.innerHTML = `
      <form id="application-form">
        <input type="text" id="first_name" /><input type="text" id="last_name" /><input type="text" id="email" />
        <input type="tel" id="phone" /><input type="file" id="resume" /><input type="file" id="cover_letter" />
        <div class="select__container"><div class="select__control"><div class="select__value-container">
          <div class="select__placeholder">Select...</div>
          <div class="select__input-container"><input id="country" type="text" role="combobox" aria-controls="lb" /></div>
        </div></div></div>
        <label for="question_1">Why us?</label><textarea id="question_1"></textarea>
        ${TRAPS}
      </form>
      ${TRAPS}`;
    for (const input of document.querySelectorAll<HTMLInputElement>('input[type="file"]')) noFiles(input);
    const container = document.querySelector(".select__container")!;
    const country = document.querySelector<HTMLInputElement>("#country")!;
    country.addEventListener("input", () => {
      container.querySelector("#lb")?.remove();
      if (country.value === "") return;
      const listbox = document.createElement("div");
      listbox.id = "lb";
      listbox.setAttribute("role", "listbox");
      const option = document.createElement("div");
      option.setAttribute("role", "option");
      option.textContent = "United States";
      option.addEventListener("click", () => {
        container.querySelector(".select__placeholder")?.replaceWith(Object.assign(document.createElement("div"), { className: "select__single-value", textContent: "United States" }));
        listbox.remove();
      });
      listbox.append(option);
      container.append(listbox);
    });

    const { hits, watcher: w } = await drive("https://job-boards.greenhouse.io/acme/jobs/1", withFiles(), "question_1");

    expect(document.querySelector<HTMLInputElement>("#first_name")!.value).toBe("Alice");
    expect(document.querySelector(".select__single-value")?.textContent).toBe("United States");
    const clicks = w.interactions.filter((i) => i.kind === "click");
    // Only entries of the country list (once per fill that chose a country), nothing else.
    expect(clicks.length).toBeGreaterThanOrEqual(1);
    expect(clicks.every((c) => c.target === "div[role=option]")).toBe(true);
    expect(w.violations()).toEqual([]);
    expect(hits).toEqual([]);
    expectTrapsUntouched();
  });

  it("Ashby (no <form>, phone and cover-letter slot found by what they are): presses nothing", async () => {
    document.body.innerHTML = `
      <div class="ashby-application-form-container">
        <div data-field-path="_systemfield_name"><label class="ashby-application-form-question-title" for="_systemfield_name">Name</label><input type="text" id="_systemfield_name" name="_systemfield_name" /></div>
        <div data-field-path="_systemfield_email"><label class="ashby-application-form-question-title" for="_systemfield_email">Email</label><input type="email" id="_systemfield_email" name="_systemfield_email" /></div>
        <div data-field-path="_systemfield_resume"><label class="ashby-application-form-question-title" for="_systemfield_resume">Resume</label><input type="file" id="_systemfield_resume" /></div>
        <div data-field-path="q-phone"><label class="ashby-application-form-question-title" for="q-phone">Phone number</label><input type="tel" id="q-phone" name="q-phone" /></div>
        <div data-field-path="cl-1"><label class="ashby-application-form-question-title" for="cl-1">Cover Letter</label><input type="file" id="cl-1" name="cl-1" /></div>
        <div data-field-path="q-why"><label class="ashby-application-form-question-title" for="q-why">Why us?</label><textarea id="q-why" name="q-why"></textarea></div>
        ${TRAPS}
      </div>`;
    for (const input of document.querySelectorAll<HTMLInputElement>('input[type="file"]')) noFiles(input);

    const { hits, watcher: w } = await drive("https://jobs.ashbyhq.com/acme/bbbb-2222/application", withFiles(), "q-why");

    expect(document.querySelector<HTMLInputElement>("#q-phone")!.value).toBe("+1-555-0100");
    expect(w.interactions.some((i) => i.kind === "dispatch")).toBe(true);
    expect(w.violations()).toEqual([]);
    expect(hits).toEqual([]);
    expectTrapsUntouched();
  });

  it("Greenhouse with hostile markup (a submit button, a link and a label called options): presses nothing", async () => {
    document.body.innerHTML = `
      <form id="application-form">
        <input type="text" id="first_name" /><input type="text" id="last_name" /><input type="text" id="email" />
        <input type="tel" id="phone" /><input type="file" id="resume" />
        <div class="select__container">
          <div class="select__control"><div class="select__value-container">
            <div class="select__placeholder">Select...</div>
            <div class="select__input-container"><input id="country" type="text" role="combobox" aria-controls="lb" /></div>
          </div></div>
          <div id="lb" role="listbox">
            <button type="submit" role="option">United States</button>
            <a href="#signin" role="option">United States</a>
            <label for="consent"><span role="option">United States</span></label>
          </div>
        </div>
        ${TRAPS}
      </form>`;
    for (const input of document.querySelectorAll<HTMLInputElement>('input[type="file"]')) noFiles(input);

    const { hits, watcher: w } = await drive("https://job-boards.greenhouse.io/acme/jobs/1", withFiles(), null);

    expect(document.querySelector<HTMLInputElement>("#first_name")!.value).toBe("Alice"); // it did real work
    expect(document.querySelector(".select__single-value")).toBeNull(); // and left the country alone
    expect(w.interactions.filter((i) => i.kind === "click")).toEqual([]);
    expect(w.violations()).toEqual([]);
    expect(hits).toEqual([]);
    expectTrapsUntouched();
  });

  it("the watcher itself fails on a plant: a click on the submit button is a violation", async () => {
    document.body.innerHTML = `<form>${TRAPS}</form>`;
    document.querySelector("form")!.addEventListener("submit", (event) => event.preventDefault());
    watcher = watchInteractions();
    document.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
    expect(watcher.violations().map((v) => v.kind)).toEqual(["click"]);
  });

  it("...and so is requestSubmit, an Enter keydown and a submit event", async () => {
    document.body.innerHTML = `<form>${TRAPS}</form>`;
    watcher = watchInteractions();
    document.querySelector("form")!.requestSubmit();
    document.querySelector("input[name=pin]")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    document.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true }));
    expect(watcher.violations().map((v) => `${v.kind}:${v.detail}`)).toEqual([
      "requestSubmit:",
      "dispatch:KeyboardEvent:keydown:Enter",
      "dispatch:Event:submit",
    ]);
  });

  it("...and so is a click on a submit button or a link that only calls itself an option", async () => {
    document.body.innerHTML = `<form><button type="submit" role="option">United States</button><a href="#x" role="option">United States</a><input type="checkbox" role="option" /></form>`;
    document.querySelector("form")!.addEventListener("submit", (event) => event.preventDefault());
    watcher = watchInteractions();
    for (const el of document.querySelectorAll<HTMLElement>("[role=option]")) el.click();
    expect(watcher.violations().map((v) => v.target)).toEqual([
      "button[type=submit][role=option]",
      "a[role=option]",
      "input[type=checkbox][role=option]",
    ]);
  });

  it("...but a click on a list option, and a plain input event, are not", async () => {
    document.body.innerHTML = `<div role="listbox"><div role="option">United States</div></div><input type="text" id="x" />`;
    watcher = watchInteractions();
    document.querySelector<HTMLElement>('[role="option"]')!.click();
    document.querySelector("#x")!.dispatchEvent(new Event("input", { bubbles: true }));
    expect(watcher.violations()).toEqual([]);
  });
});
