// The global rows: the ones that apply on every page before the page's own rows. They are tried in
// the order of the list below. G2b (an import is waiting) comes before G2 (no active profile)
// because it is G2 with one more fact: after G2 it could never be reached.
//
//   LG  legal pages        no band
//   G0  signed out         sign in
//   G1  setup check failed try again
//   --  setup still loading: wait, so no step is lit and replaced a moment later (the profile read
//       always; the credentials read only when the page supplied it)
//   G2b no profile, an import waiting
//   G2  no active profile
//   F1  the page's own data request failed
//
// There is no row for a missing model key here: a key can belong to any of several providers, so
// what counts as "set" is for the page that reads the credentials to decide.

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

// Text a caller handed in, ready to be drawn: trimmed, and null when nothing is left.
function clean(text: string | undefined): string | null {
  const trimmed = text?.trim() ?? "";
  return trimmed === "" ? null : trimmed;
}

// The why lines for one or more failed requests: "GET /a failed", then the time. Calls that all
// failed at the same time share one time line; otherwise each call carries its own ("GET /a failed
// 09:14"), so a time is never read as belonging to a request it does not. A blank call or time is
// left out rather than drawn as a dangling separator.
function failureLines(failures: readonly { call: string; at: string }[]): string[] {
  const entries = failures.map((failure) => ({ call: clean(failure.call), at: clean(failure.at) }));
  if (entries.every((entry) => entry.at === entries[0].at)) {
    const calls = entries.flatMap((entry) => (entry.call === null ? [] : [`${entry.call} failed`]));
    return entries[0].at === null ? calls : [...calls, entries[0].at];
  }
  return entries.flatMap(({ call, at }) => {
    if (call === null) return at === null ? [] : [at];
    return [at === null ? `${call} failed` : `${call} failed ${at}`];
  });
}

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
      why: failureLines(failed),
      tone: "failure",
    };
  },
};

// A signed-in page is held until the profile read has answered: G2 and the page's rows depend on
// it, and a failure would replace whatever was lit. The credentials read is waited for only when
// the page made it (it supplied the field and it is still loading): no row reads its value yet, so
// a page that never calls it must not be held, or be made to invent a result, just to pass here.
const SETUP_LOADING: NextStepRow = {
  id: "SETUP",
  match: (input) => {
    if (!inApp(input)) return null;
    const unread = (check: SetupCheck<unknown> | undefined) => check === undefined || check.state === "loading";
    return unread(input.profile) || input.credentials?.state === "loading" ? "wait" : null;
  },
};

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
      action: { kind: "link", label: "Set up your profile", to: "/profile" },
      why: ["no active profile"],
    };
  },
};

const F1: NextStepRow = {
  id: "F1",
  match: (input) => {
    const failure = input.pageFailure;
    if (!inApp(input) || failure === null || failure === undefined) return null;
    // `what` is the start of a sentence: its own full stop is dropped so it is not doubled, and a
    // blank one falls back to the one thing known, that the page's data did not load.
    const what = clean(failure.what)?.replace(/[.\s]+$/, "") || "Couldn't load this page";
    const step: NextStep = {
      sentence: `${what}. ${NOTHING_LOST}`,
      action: { kind: "button", label: "Try again", intent: failure.intent ?? "retry" },
      why: failureLines([failure]),
      tone: "failure",
    };
    return step;
  },
};

export const GLOBAL_ROWS: readonly NextStepRow[] = [LG, G0, G1, SETUP_LOADING, G2B, G2, F1];
