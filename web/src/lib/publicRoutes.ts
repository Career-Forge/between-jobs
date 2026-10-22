// Which screen a visitor gets: one pure function of three facts (is there a session, is the
// session still being looked up, which path is this), so the whole table can be run in a test.
// App.tsx asks it once per render and only draws the answer.
//
//   loading                      -> "boot"           a blank page: nothing is decided yet
//   signed out
//     "/"                        -> "landing"        the public home page ...
//     "/how-it-works", "/open-source" -> "how_it_works" / "open_source"  the two explainer pages
//     "/privacy", "/terms"       -> "privacy" / "terms"  the two legal pages
//     "/login"                   -> "login"
//     any other path             -> "login"          a deep link such as /applications/123 lands
//                                                    on sign-in, and after signing in the same
//                                                    URL opens that page (the shell takes over
//                                                    at the same path)
//   signed in
//     "/login"                   -> "home_redirect"  a signed-in person has no use for the form
//     any other path             -> "app"            the signed-in shell, which routes the rest
//                                                    -- "/privacy" and "/terms" included
//
// WHY THE LEGAL AND EXPLAINER PAGES LIVE INSIDE THE SHELL WHEN SIGNED IN. Someone who opens the
// Privacy Policy from the nav footer is in the middle of using the app; the shell keeps the
// navigation and the sign-out button in reach, where a bare public page would strand them. A
// signed-in person who follows a shared link to /how-it-works or /open-source gets the same
// treatment rather than a not-found page. Signed out, there is no shell, so the same text is drawn
// on the public layout.
//
// ONE EXCEPTION TO "/" BEING THE LANDING PAGE. The auth server sends a visitor back to the
// site's root with the error in the URL when a link fails: a dead password-reset link when the
// project does not allow-list /update-password, a cancelled Google consent screen. Those
// visitors are on the sign-in page today, and it is where they belong (the sign-in page shows
// the dead-link sentence and the reset form; for any other error it shows the ordinary
// sign-in). So a signed-out "/" that carries an auth error is "login", not "landing".
//
// SPELLING. A path is matched exactly, the way the rest of the app treats paths (see
// setupRequired.ts: "/profile/" and "/Profile" are not the routed spelling):
//   - a query string or fragment is ignored (the shell passes `pathname`, which has neither,
//     but a caller may hand over a whole address);
//   - trailing slashes are ignored: "/privacy/" is "/privacy";
//   - case matters: "/Privacy" is NOT a route, so a signed-out visitor to it gets the sign-in
//     page like any other unknown path (the deep-link rule above), not the policy;
//   - nothing is percent-decoded: "/priv%61cy" is not "/privacy".
// Signed in, an unknown spelling reaches the shell, whose router matches the paths it has
// without regard to case, exactly as it did before this module existed.

import { LOGIN_REGISTER_QUERY, UPDATE_PASSWORD_PATH, urlCarriesAuthError, type LoginMode } from "./passwordReset";
import { LEGACY_INTEGRATIONS_PATH, SETTINGS_PATH } from "./settingsPaths";

export const LANDING_PATH = "/";
export const LOGIN_PATH = "/login";
export const PRIVACY_PATH = "/privacy";
export const TERMS_PATH = "/terms";
export const HOW_IT_WORKS_PATH = "/how-it-works";
export const OPEN_SOURCE_PATH = "/open-source";
// The tester-programme enrollment page. Signed out, it is like any other deep link (sign in,
// then the same address opens it); the gate that sends people to it is lib/enrollment.ts.
export const ENROLL_PATH = "/enroll";
// What that page is called in its heading and in the document title.
export const ENROLL_PAGE_NAME = "Tester programme";

// Where the landing page's "Create account" link goes: the sign-in page, opened on its
// registration form.
export const REGISTER_PATH = `${LOGIN_PATH}${LOGIN_REGISTER_QUERY}`;

// "/privacy/?a=1#b" -> "/privacy"; "" and "//" -> "/".
export function normalizePath(path: string): string {
  const end = path.search(/[?#]/);
  const bare = end === -1 ? path : path.slice(0, end);
  const trimmed = bare.replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

export type AppView =
  | "boot"
  | "landing"
  | "login"
  | "how_it_works"
  | "open_source"
  | "privacy"
  | "terms"
  | "home_redirect"
  | "app";

export interface ViewInput {
  signedIn: boolean;
  // True until supabase-js has said whether there is a session.
  loading: boolean;
  pathname: string;
  search?: string;
  hash?: string;
}

export function appView(input: ViewInput): AppView {
  if (input.loading) return "boot";

  const path = normalizePath(input.pathname);
  if (input.signedIn) return path === LOGIN_PATH ? "home_redirect" : "app";

  switch (path) {
    case LANDING_PATH:
      return urlCarriesAuthError(input.hash ?? "", input.search ?? "") ? "login" : "landing";
    case HOW_IT_WORKS_PATH:
      return "how_it_works";
    case OPEN_SOURCE_PATH:
      return "open_source";
    case PRIVACY_PATH:
      return "privacy";
    case TERMS_PATH:
      return "terms";
    default:
      // "/login", "/update-password" and every deep link.
      return "login";
  }
}

// ── the document title ─────────────────────────────────────────────────────
//
// Every screen needs a title of its own: a tab strip, the history list and a screen reader's
// "page title" announcement are how a visitor tells /privacy from /terms from the sign-in page
// (WCAG 2.4.2, Page Titled). The shell asks documentTitleFor once per render and a hook writes
// the answer into document.title; the sign-in page, whose "Sign in" / "Create account" /
// "Reset your password" state lives inside it, names itself with loginPageTitle.

export const SITE_NAME = "Between Jobs";

// What the two explainer pages are called in their headings, the header's tabs and the document title.
export const HOW_IT_WORKS_PAGE_NAME = "How it works";
export const OPEN_SOURCE_PAGE_NAME = "Open source";

// The project writes " -- " where other text would put a dash.
export function pageTitle(page: string | null): string {
  return page === null ? SITE_NAME : `${page} -- ${SITE_NAME}`;
}

// null means "leave the title alone": nothing is decided yet (boot), a redirect is about to
// replace the screen (home_redirect), or the sign-in page, which sets its own.
export function documentTitleFor(view: AppView, pathname: string): string | null {
  switch (view) {
    case "boot":
    case "login":
    case "home_redirect":
      return null;
    case "landing":
      return pageTitle(null);
    case "how_it_works":
      return pageTitle(HOW_IT_WORKS_PAGE_NAME);
    case "open_source":
      return pageTitle(OPEN_SOURCE_PAGE_NAME);
    case "privacy":
      return pageTitle("Privacy Policy");
    case "terms":
      return pageTitle("Terms of Service");
    case "app": {
      // The signed-in shell draws the legal pages too, and every other page of it must put the
      // plain title back when the visitor leaves them.
      const path = normalizePath(pathname);
      if (path === PRIVACY_PATH) return pageTitle("Privacy Policy");
      if (path === TERMS_PATH) return pageTitle("Terms of Service");
      if (path === HOW_IT_WORKS_PATH) return pageTitle(HOW_IT_WORKS_PAGE_NAME);
      if (path === OPEN_SOURCE_PATH) return pageTitle(OPEN_SOURCE_PAGE_NAME);
      if (path === ENROLL_PATH) return pageTitle(ENROLL_PAGE_NAME);
      const name = appPageName(path);
      if (name !== null) return pageTitle(name);
      // An address the router has no page for is the not-found page (pages/NotFound.tsx), and says so in the tab. A
      // spelling the router does match but this table does not name (another case of a legal page, the old
      // integrations address, the password form) keeps the plain title.
      return pageTitle(ROUTED_WITHOUT_A_NAME.has(path.toLowerCase()) ? null : NOT_FOUND_PAGE_NAME);
    }
  }
}

// The signed-in pages, by address: what each is called in the tab, the history list and a screen
// reader's page announcement. The router matches these without regard to case, so the table does too.
// A tracked application's workspace is one address per application and is titled "Application".
const APP_PAGE_NAMES: Readonly<Record<string, string>> = {
  "/": "Today",
  "/discover": "Discover",
  "/hiring-signals": "Hiring signals",
  "/applications": "Applications",
  "/practice": "Practice",
  "/profile": "Profile",
  [SETTINGS_PATH]: "Settings",
};
const WORKSPACE_PATH = /^\/applications\/[^/]+$/i;

// What the not-found page is called in its heading and in the document title.
export const NOT_FOUND_PAGE_NAME = "Page not found";

// Addresses the router matches (App.tsx) that the table above does not name: the legal and explainer pages and the
// tester programme (titled by their own rules, which are exact about case), the old integrations address (it
// redirects) and the password form. Lower case, like the table.
const ROUTED_WITHOUT_A_NAME: ReadonlySet<string> = new Set([PRIVACY_PATH, TERMS_PATH, HOW_IT_WORKS_PATH, OPEN_SOURCE_PATH, ENROLL_PATH, UPDATE_PASSWORD_PATH, LEGACY_INTEGRATIONS_PATH]);

/** What a signed-in page at this address is called (its heading), or null where there is no such page. */
export function appPageNameFor(pathname: string): string | null {
  return appPageName(normalizePath(pathname));
}

function appPageName(path: string): string | null {
  const lower = path.toLowerCase();
  if (Object.prototype.hasOwnProperty.call(APP_PAGE_NAMES, lower)) return APP_PAGE_NAMES[lower];
  return WORKSPACE_PATH.test(path) ? "Application" : null;
}

export function loginPageTitle(mode: LoginMode): string {
  switch (mode) {
    case "register":
      return pageTitle("Create account");
    case "reset":
      return pageTitle("Reset your password");
    case "sign_in":
      return pageTitle("Sign in");
  }
}
