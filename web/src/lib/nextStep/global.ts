// The rule table's global rows: the ones that apply on every page before the page's own rows.
// Order is the table's precedence, with one correction: G2b (an import is waiting) is checked
// before G2 (no active profile), because it is G2 with one more fact, and after G2 it could never
// be reached.
//
//   LG  legal pages        no band
//   G0  signed out         sign in
//   G1  setup check failed try again
//   --  setup still loading: wait, so no step is lit and replaced a moment later
//   G2b no profile, an import waiting
//   G2  no active profile
//   F1  the page's own data request failed
//
// G3 (no model key) is not here: the model key is no longer OpenRouter-only, so what counts as
// "set" is a decision for the page that reads the credentials.

import type { NextStep, NextStepInput, NextStepRow, SetupCheck } from "./types";

// Pages that are outside the signed-in app. A signed-out visitor to any other address is shown the
// sign-in page, so G0 covers every page except these.
export const PUBLIC_PAGES: readonly string[] = ["landing", "privacy", "terms"];
// The legal pages have no "now", so they have no next step -- signed in or out.
export const LEGAL_PAGES: readonly string[] = ["privacy", "terms"];

// The id the sign-in page gives its form, so the band's button can submit it from outside.
export const AUTH_FORM_ID = "auth-form";

export const SIGNED_OUT_WHY = "you're signed out";
const NOTHING_LOST = "You didn't do anything wrong, and nothing was lost.";

// The signed-in app, as opposed to the public pages: where the setup checks and the page's own
// data failures apply.
function inApp(input: NextStepInput): boolean {
  return input.signedIn && !PUBLIC_PAGES.includes(input.page);
}

const LG: NextStepRow = {
  id: "LG",
  match: (input) => (LEGAL_PAGES.includes(input.page) ? "none" : null),
};

const G0: NextStepRow = {
  id: "G0",
  match: (input) => {
    if (input.signedIn || PUBLIC_PAGES.includes(input.page)) return null;
    return {
      sentence: "Sign in to your job search workspace.",
      action: {
        kind: "submit",
        label: input.authMode === "register" ? "Create account" : "Sign in",
        form: AUTH_FORM_ID,
      },
      why: [SIGNED_OUT_WHY],
    };
  },
};

const G1: NextStepRow = {
  id: "G1",
  match: (input) => {
    if (!inApp(input)) return null;
    const failed = [input.profile, input.credentials].flatMap((check) => (check?.state === "failed" ? [check] : []));
    if (failed.length === 0) return null;
    return {
      sentence: `Couldn't check your setup. ${NOTHING_LOST}`,
      action: { kind: "button", label: "Try again", intent: "retry-setup" },
      why: [...failed.map((check) => `${check.call} failed`), failed[0].at],
      tone: "failure",
    };
  },
};

// Every signed-in page is held until both setup reads have answered: G2 and the page's rows depend
// on them, and a failure would replace whatever was lit.
const SETUP_LOADING: NextStepRow = {
  id: "SETUP",
  match: (input) => {
    if (!inApp(input)) return null;
    const unread = (check: SetupCheck<unknown> | undefined) => check === undefined || check.state === "loading";
    return unread(input.profile) || unread(input.credentials) ? "wait" : null;
  },
};

// On the profile page itself the button goes to the import card there, not to the page it is on.
function profileTarget(input: NextStepInput, hash: string): string {
  return input.page === "profile" ? `/profile#${hash}` : "/profile";
}

const G2B: NextStepRow = {
  id: "G2b",
  match: (input) => {
    if (!inApp(input) || input.profile?.state !== "ready" || input.profile.value.active || !input.profile.value.importWaiting) return null;
    return {
      sentence: "Your import is waiting. Check every field, then activate.",
      action: { kind: "link", label: "Review the import", to: "/profile#import-review" },
      why: ["import waiting for review", "not active until you confirm"],
    };
  },
};

const G2: NextStepRow = {
  id: "G2",
  match: (input) => {
    if (!inApp(input) || input.profile?.state !== "ready" || input.profile.value.active) return null;
    return {
      sentence: "Everything here starts from your profile. Import it first.",
      action: { kind: "link", label: "Set up your profile", to: profileTarget(input, "set-up") },
      why: ["no active profile"],
    };
  },
};

const F1: NextStepRow = {
  id: "F1",
  match: (input) => {
    const failure = input.pageFailure;
    if (!inApp(input) || failure === null || failure === undefined) return null;
    const step: NextStep = {
      sentence: `${failure.what}. ${NOTHING_LOST}`,
      action: { kind: "button", label: "Try again", intent: failure.intent ?? "retry" },
      why: [`${failure.call} failed`, failure.at],
      tone: "failure",
    };
    return step;
  },
};

export const GLOBAL_ROWS: readonly NextStepRow[] = [LG, G0, G1, SETUP_LOADING, G2B, G2, F1];
