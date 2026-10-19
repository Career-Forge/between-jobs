// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_FORM_ID } from "../lib/nextStep";
import { MIN_PASSWORD_LENGTH } from "../lib/passwordReset";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { buttonNamed, click, query, render } from "../testing/render";
import Login from "./Login";

// The sign-in and register screen as a person meets it, run in a DOM: its labels and field attributes,
// the one cyan fill (the next-step band's button, which submits the form from outside it), the secondary
// Google button, and every behavior the screen had before it was restyled -- the error line, the double
// submit guard, the confirmation notice, the password reset. Which mode opens, and what a dead reset link
// leads to, is lib/passwordReset.ts's own test.

const auth = vi.hoisted(() => ({
  signIn: vi.fn(),
  signUp: vi.fn(),
  signInWithGoogle: vi.fn(),
  resetPassword: vi.fn(),
}));

// The real auth module pulls in the Supabase client, which throws at import without env vars.
vi.mock("../auth", () => ({ useAuth: () => auth }));

function open(search = "") {
  window.history.replaceState({}, "", `/login${search}`);
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>,
  ).container;
}

function typeInto(input: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit(form: HTMLFormElement) {
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const CYAN = ".bj-primary, .bj-button-link-primary";

beforeEach(() => {
  auth.signIn.mockReset().mockResolvedValue(null);
  auth.signUp.mockReset().mockResolvedValue({ kind: "signed_in" });
  auth.signInWithGoogle.mockReset().mockResolvedValue(null);
  auth.resetPassword.mockReset();
});

describe.each([
  ["sign-in", "", "Sign in", "current-password", "Sign in to your job search workspace."],
  ["register", "?mode=register", "Create account", "new-password", "Create an account to start your job search workspace."],
])("the %s form", (_name, search, buttonLabel, passwordAutocomplete, bandSentence) => {
  it("labels its two fields, so a screen reader and a tap on the label both reach them, and asks the phone for the right keyboard", () => {
    const root = open(search);
    const email = query<HTMLInputElement>(root, 'input[type="email"]');
    const password = query<HTMLInputElement>(root, 'input[type="password"]');
    for (const [input, name] of [
      [email, "Email"],
      [password, "Password"],
    ] as const) {
      const label = query(root, `label[for="${input.id}"]`);
      expect(text(label)).toBe(name);
      expect(input.required).toBe(true);
    }
    expect(email.getAttribute("autocomplete")).toBe("email");
    expect(email.getAttribute("autocapitalize")).toBe("none");
    expect(email.getAttribute("spellcheck")).toBe("false");
    expect(password.getAttribute("autocomplete")).toBe(passwordAutocomplete);
  });

  it("holds exactly one cyan fill, and it is the page's submit button, which sits in the band and names the form", () => {
    const root = open(search);
    const fills = root.querySelectorAll(CYAN);
    expect(fills).toHaveLength(1);
    const button = fills[0] as HTMLButtonElement;
    expect(button.tagName).toBe("BUTTON");
    expect(button.type).toBe("submit");
    expect(text(button)).toBe(buttonLabel);
    expect(button.getAttribute("form")).toBe(AUTH_FORM_ID);
    expect(button.closest("section")?.getAttribute("aria-label")).toBe("Next step");
    const form = query<HTMLFormElement>(root, `form#${AUTH_FORM_ID}`);
    expect(form.contains(button)).toBe(false);
    // The browser's own association: the form's submit button is the one that names it.
    expect(form.elements.length).toBeGreaterThan(1);
    expect(button.form).toBe(form);
  });

  it("puts the band, in the signed-out words, above the card", () => {
    const root = open(search);
    const band = query(root, 'section[aria-label="Next step"]');
    // The sentence agrees with the form: nobody on the register screen is told to sign in.
    expect(text(query(band, ".bj-band-sentence"))).toBe(bandSentence);
    expect(text(query(band, ".bj-band-why"))).toBe("why: you're signed out");
    expect(band.compareDocumentPosition(query(root, ".bj-login-card")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("offers Continue with Google as an outlined button, never a second fill", () => {
    const root = open(search);
    const google = buttonNamed(root, "Continue with Google");
    expect(google.classList.contains("bj-google-button")).toBe(true);
    expect(google.type).toBe("button");
    expect(google.matches(CYAN)).toBe(false);
    click(google);
    expect(auth.signInWithGoogle).toHaveBeenCalledTimes(1);
  });

  it("keeps the consent line above the Google button, and the legal links on the card, opening in a new tab", () => {
    const root = open(search);
    const notice = query(root, ".bj-consent-notice");
    const google = buttonNamed(root, "Continue with Google");
    expect(notice.compareDocumentPosition(google) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const legal = [...query(root, ".bj-login-legal").querySelectorAll("a")];
    expect(legal.map((link) => link.getAttribute("href"))).toEqual(["/privacy", "/terms"]);
    for (const link of legal) {
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("aria-label")).toContain("(opens in a new tab)");
    }
  });

  it("starts with Skip to next step, which points at the band's button, and has one <main> and one h1", () => {
    const root = open(search);
    const [skip, content] = [...root.querySelectorAll<HTMLAnchorElement>("a.bj-skip-link")];
    expect(text(skip)).toBe("Skip to next step");
    expect(root.firstElementChild).toBe(skip);
    expect(root.querySelector(skip.getAttribute("href") ?? "")).toBe(query(root, CYAN));
    // ... then Skip to content, which lands on the page's one <main>.
    expect(text(content)).toBe("Skip to content");
    expect(skip.nextElementSibling).toBe(content);
    expect(root.querySelector(content.getAttribute("href") ?? "")).toBe(query(root, "main"));
    expect(query(root, "main").getAttribute("tabindex")).toBe("-1");
    expect(root.querySelectorAll("main")).toHaveLength(1);
    expect(root.querySelectorAll("h1")).toHaveLength(1);
    expect(text(query(root, "h1"))).toBe("Between Jobs");
  });

  it("has no serious or critical axe violation", async () => {
    await expectNoSeriousA11yViolations(open(search));
  });
});

describe("the register form", () => {
  it("opens on the card's own heading, names the password's minimum, and ties that line to the field", () => {
    const root = open("?mode=register");
    expect(text(query(root, ".bj-login-card h2"))).toBe("Create your account");
    const password = query<HTMLInputElement>(root, 'input[type="password"]');
    expect(password.minLength).toBe(MIN_PASSWORD_LENGTH);
    const hint = query(root, `#${CSS.escape(password.getAttribute("aria-describedby") ?? "")}`);
    expect(text(hint)).toBe(`At least ${MIN_PASSWORD_LENGTH} characters.`);
  });

  it("says to check email when the server wants the address confirmed, and goes back to the sign-in form", async () => {
    auth.signUp.mockResolvedValue({ kind: "confirmation_required" });
    const root = open("?mode=register");
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    typeInto(query(root, 'input[type="password"]'), "a-long-enough-one");
    await submit(query(root, "form"));
    expect(auth.signUp).toHaveBeenCalledWith("someone@example.test", "a-long-enough-one");
    expect(text(query(root, '.bj-login-card [role="status"]:not(.bj-login-busy)'))).toBe("Check your email to confirm your account, then sign in.");
    expect(text(query(root, ".bj-login-card h2"))).toBe("Sign in");
  });

  it("shows the server's refusal in an alert", async () => {
    auth.signUp.mockResolvedValue({ kind: "error", message: "User already registered" });
    const root = open("?mode=register");
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    typeInto(query(root, 'input[type="password"]'), "a-long-enough-one");
    await submit(query(root, "form"));
    expect(text(query(root, '[role="alert"]'))).toBe("User already registered");
  });
});

describe("signing in", () => {
  it("sends the address and the password, and shows nothing when it works", async () => {
    const root = open();
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    typeInto(query(root, 'input[type="password"]'), "hunter22");
    await submit(query(root, "form"));
    expect(auth.signIn).toHaveBeenCalledWith("someone@example.test", "hunter22");
    expect(root.querySelector('[role="alert"]')).toBeNull();
  });

  it("shows the server's message in an alert, in the server's own words", async () => {
    auth.signIn.mockResolvedValue("Invalid login credentials");
    const root = open();
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    typeInto(query(root, 'input[type="password"]'), "wrong");
    await submit(query(root, "form"));
    const alert = query(root, '[role="alert"]');
    expect(text(alert)).toBe("Invalid login credentials");
    expect(alert.classList.contains("bj-error")).toBe(true);
  });

  it("shows the band's button busy while the request is out, and ignores a second press", async () => {
    let resolve: (value: null) => void = () => undefined;
    auth.signIn.mockReturnValue(new Promise<null>((done) => (resolve = done)));
    const root = open();
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    typeInto(query(root, 'input[type="password"]'), "hunter22");
    const form = query<HTMLFormElement>(root, "form");
    await submit(form);
    await submit(form);
    expect(auth.signIn).toHaveBeenCalledTimes(1);
    expect(text(query(root, ".bj-login-busy"))).toBe("Signing in...");
    // The band's own button carries the state: aria-disabled (still focusable), reading what is under way.
    const band = query(root, 'section[aria-label="Next step"]');
    const button = query<HTMLButtonElement>(band, "button.bj-band-action");
    expect(text(button)).toBe("Signing in...");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.disabled).toBe(false);
    click(button);
    expect(auth.signIn).toHaveBeenCalledTimes(1);
    await act(async () => resolve(null));
    expect(text(query(root, ".bj-login-busy"))).toBe("");
    expect(text(query(band, "button.bj-band-action"))).toBe("Sign in");
    expect(query(band, "button.bj-band-action").hasAttribute("aria-disabled")).toBe(false);
  });

  it("shows an error from Google's redirect and lets the person try again", async () => {
    auth.signInWithGoogle.mockResolvedValue("Google is not configured");
    const root = open();
    click(buttonNamed(root, "Continue with Google"));
    await act(async () => undefined);
    expect(text(query(root, '[role="alert"]'))).toBe("Google is not configured");
    expect(buttonNamed(root, "Continue with Google").disabled).toBe(false);
  });

  it("switches between the two forms and back, and clears what the last one said", async () => {
    auth.signIn.mockResolvedValue("Invalid login credentials");
    const root = open();
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    typeInto(query(root, 'input[type="password"]'), "wrong");
    await submit(query(root, "form"));
    expect(root.querySelector('[role="alert"]')).not.toBeNull();
    click(buttonNamed(root, "Need an account? Register"));
    expect(root.querySelector('[role="alert"]')).toBeNull();
    expect(text(query(root, ".bj-login-card h2"))).toBe("Create your account");
    expect(text(query(root, CYAN))).toBe("Create account");
    click(buttonNamed(root, "Already have an account? Sign in"));
    expect(text(query(root, CYAN))).toBe("Sign in");
  });
});

describe("the password reset form", () => {
  function reset() {
    const root = open();
    click(buttonNamed(root, "Forgot password?"));
    return root;
  }

  it("keeps its own cyan button and draws no band, since the band's action would name a form that is not there", () => {
    const root = reset();
    expect(text(query(root, ".bj-login-card h2"))).toBe("Reset your password");
    expect(root.querySelector('section[aria-label="Next step"]')).toBeNull();
    // No band, so no "Skip to next step": only Skip to content, onto the <main>.
    expect([...root.querySelectorAll("a.bj-skip-link")].map((link) => text(link))).toEqual(["Skip to content"]);
    const fills = root.querySelectorAll(CYAN);
    expect(fills).toHaveLength(1);
    expect(text(fills[0])).toBe("Send reset link");
    expect(root.querySelectorAll("main")).toHaveLength(1);
    expect(text(query(root, "h1"))).toBe("Between Jobs");
  });

  it("sends a link for the address typed, and says the same thing whatever the answer", async () => {
    auth.resetPassword.mockResolvedValue({ ok: true });
    const root = reset();
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    await submit(query(root, "form"));
    expect(auth.resetPassword).toHaveBeenCalledTimes(1);
    expect(auth.resetPassword.mock.calls[0][0]).toBe("someone@example.test");
  });

  // The button that was pressed leaves with the screen it was on: focus must land somewhere the person can
  // use, not at the top of the document.
  it("puts keyboard focus in the email field when it opens, on the confirmation once a link is sent, and in the sign-in email field when it closes", async () => {
    auth.resetPassword.mockResolvedValue({ kind: "sent", message: "If an account exists for that address, a reset link is on its way." });
    const root = open();
    const forgot = buttonNamed(root, "Forgot password?");
    forgot.focus();
    click(forgot);
    expect(document.activeElement).toBe(query(root, 'input[type="email"]'));
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    await submit(query(root, "form"));
    expect(document.activeElement).toBe(query(root, '[role="status"]'));
    click(buttonNamed(root, "Back to sign in"));
    expect(document.activeElement).toBe(query(root, 'input[type="email"]'));
  });

  it("does not take focus when the page arrives with the form already open under a dead-link sentence", () => {
    const root = open("#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired");
    expect(text(query(root, ".bj-login-card h2"))).toBe("Reset your password");
    expect(document.activeElement).toBe(document.body);
  });

  it("keeps focus on Send reset link while the request is out, with the field read-only", async () => {
    let finish: (outcome: unknown) => void = () => undefined;
    auth.resetPassword.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const root = reset();
    typeInto(query(root, 'input[type="email"]'), "someone@example.test");
    const send = buttonNamed(root, "Send reset link");
    send.focus();
    await submit(query(root, "form"));
    const busy = buttonNamed(root, "Sending...");
    expect(busy).toBe(send);
    expect(busy.disabled).toBe(false);
    expect(busy.getAttribute("aria-disabled")).toBe("true");
    expect(query<HTMLInputElement>(root, 'input[type="email"]').readOnly).toBe(true);
    expect(document.activeElement).toBe(send);
    await submit(query(root, "form"));
    expect(auth.resetPassword).toHaveBeenCalledTimes(1);
    await act(async () => finish({ kind: "error", message: "Could not send." }));
  });

  it("goes back to sign-in", () => {
    const root = reset();
    click(buttonNamed(root, "Back to sign in"));
    expect(text(query(root, ".bj-login-card h2"))).toBe("Sign in");
    expect(root.querySelector('section[aria-label="Next step"]')).not.toBeNull();
  });

  it("has no serious or critical axe violation", async () => {
    await expectNoSeriousA11yViolations(reset());
  });
});
