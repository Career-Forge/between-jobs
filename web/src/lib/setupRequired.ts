// SETUP_REQUIRED, turned into something a person can act on.
//
// The API answers 409 SETUP_REQUIRED when a feature needs something the person has
// not set up yet -- an active profile, a model key, a search key -- and says where
// to fix it (`settings_path`) and what is missing (`capability`, `missing`). Until
// now the web app showed only the sentence; a first-time user was left to guess
// which page to open. This module decides what to show: the server's sentence plus
// one link to the page that fixes it.
//
// THE LINK IS A STRING OUT OF A RESPONSE BODY that ends up in a router <Link>, so it
// is held to a narrow rule instead of being trusted: it must be one of the app's own
// routed paths (ROUTED_SETTINGS_PATHS, checked against App.tsx by a test), optionally
// followed by the single `?capability=<token>` query the credential resolver adds,
// and nothing else. Anything that does not fit -- an external URL, a protocol-relative
// one, a `javascript:` URL, a backslash, a control character, an unrouted path -- is
// not "repaired" into something nearby: there is simply no link, and the message
// stands alone. A missing path is the same: the label is never a reason to guess a
// destination.
//
// Pure module: an error is read structurally (ApiError in api.ts carries these
// fields, but importing it would pull in the Supabase client, which throws at import
// time without env vars), and there is no React here -- the component that renders
// the result is components/SetupRequiredNotice.tsx.

import { friendlyApiMessage } from "./rateLimitMessage";

export const SETUP_REQUIRED_CODE = "SETUP_REQUIRED";

// The sentence used when the server sent none.
export const GENERIC_SETUP_MESSAGE = "Something needs to be set up before this will work.";

// The app's own signed-in pages a "fix it here" link may point at (their paths are
// the `<Route path>` values in App.tsx; setupRequired.test.ts fails if one is not).
// "/update-password" is a routed page too but is part of signing in, never the place a
// missing setting is fixed, so it is not here. A page with a path parameter
// (/applications/:id) cannot be named by a fixed string and is not here either.
export const ROUTED_SETTINGS_PATHS: readonly string[] = [
  "/",
  "/discover",
  "/hiring-signals",
  "/applications",
  "/practice",
  "/profile",
  "/profile/integrations",
];

const ROUTED = new Set(ROUTED_SETTINGS_PATHS);

const MAX_LINK_LENGTH = 200;
// Whitespace, control characters (C0 and C1) and the backslash, which browsers read
// as a slash in a URL.
const UNSAFE_LINK_CHARACTERS = /[\u0000- \u007f-\u009f\\]/;
// The one query the resolver adds: the capability that needs the key.
const CAPABILITY_QUERY = /^capability=([a-z][a-z0-9_]{0,63})$/;
// A capability name as the server writes them.
const CAPABILITY_TOKEN = /^[a-z][a-z0-9_]{0,63}$/;

// The link, as it may be rendered, or null. See the header for what is refused.
export function safeSettingsLink(path: unknown): string | null {
  if (typeof path !== "string" || path.length === 0 || path.length > MAX_LINK_LENGTH) {
    return null;
  }
  if (!path.startsWith("/") || path.startsWith("//")) return null;
  if (UNSAFE_LINK_CHARACTERS.test(path) || path.includes("://") || path.includes("#")) return null;

  const queryStart = path.indexOf("?");
  const pathname = queryStart === -1 ? path : path.slice(0, queryStart);
  if (!ROUTED.has(pathname)) return null;
  if (queryStart === -1) return pathname;

  const query = CAPABILITY_QUERY.exec(path.slice(queryStart + 1));
  return query === null ? null : `${pathname}?capability=${query[1]}`;
}

function pathnameOf(link: string): string {
  const queryStart = link.indexOf("?");
  return queryStart === -1 ? link : link.slice(0, queryStart);
}

// ── what the server said was missing ───────────────────────────────────────

export interface SetupFields {
  settingsPath?: string;
  capability?: string;
  missing?: readonly string[];
}

// The three setup fields of an error envelope (`{"error": {...}}`'s inner object), each
// kept only when it has the right type. An ordinary error carries them as nulls, and a
// body that is not an object at all has none.
export function parseSetupFields(envelopeError: unknown): SetupFields {
  if (typeof envelopeError !== "object" || envelopeError === null || Array.isArray(envelopeError)) {
    return {};
  }
  const raw = envelopeError as Record<string, unknown>;
  const fields: SetupFields = {};
  if (typeof raw.settings_path === "string") fields.settingsPath = raw.settings_path;
  if (typeof raw.capability === "string") fields.capability = raw.capability;
  if (Array.isArray(raw.missing)) {
    fields.missing = raw.missing.filter((item): item is string => typeof item === "string");
  }
  return fields;
}

// ── labels ─────────────────────────────────────────────────────────────────

const INTEGRATIONS = "/profile/integrations";

interface Target {
  // The page the label describes. A label is only used on a link to this page: a
  // "Finish your profile" label on a link to Integrations would be a lie.
  path: string;
  label: string;
}

const MODEL_KEY: Target = { path: INTEGRATIONS, label: "Add a model key in Integrations" };
const PROFILE: Target = { path: "/profile", label: "Finish your profile" };
// Apollo and Hunter are tried together for one lookup, so either one missing is the same fix.
const ENRICHMENT_KEY: Target = { path: INTEGRATIONS, label: "Add an Apollo or Hunter key in Integrations" };

// What the server's `missing` list names (credential_resolver.py and the routes that
// raise SETUP_REQUIRED themselves). The first token that is known decides, because
// `missing` is more exact than `capability`: company intel raises this error for a
// missing model key AND for a missing search key, and only `missing` says which.
// A Map, so an odd token such as "constructor" is simply not found.
const TARGET_BY_MISSING = new Map<string, Target>([
  ["profile_version", PROFILE],
  ["execution_mode", MODEL_KEY],
  ["provider", MODEL_KEY],
  ["model", MODEL_KEY],
  ["credential", MODEL_KEY],
  ["apollo_credential", ENRICHMENT_KEY],
  ["hunter_credential", ENRICHMENT_KEY],
  ["exa_credential", { path: INTEGRATIONS, label: "Add an Exa key in Integrations" }],
  ["gmail_oauth", { path: INTEGRATIONS, label: "Connect Gmail in Integrations" }],
  ["firecrawl_credential", { path: INTEGRATIONS, label: "Add a Firecrawl key in Integrations" }],
  [
    "you_com_or_firecrawl_credential",
    { path: INTEGRATIONS, label: "Add a You.com or Firecrawl key in Integrations" },
  ],
  // Several providers would do here (Brave, Serper, Firecrawl, You.com).
  ["search_credential", { path: INTEGRATIONS, label: "Add a search key in Integrations" }],
]);

// The capabilities that run on the person's own model, so a failure to resolve one
// (the resolver sends `?capability=<name>` on its link) is fixed by the model key.
const MODEL_KEY_CAPABILITIES = [
  "job_scoring",
  "prepare_application",
  "positioning_brief",
  "interview_practice",
  "outreach_writer",
  "contact_research",
  "company_intel",
  "warm_path_events",
  "application_answer_generation",
];

const TARGET_BY_CAPABILITY = new Map<string, Target>([
  ["profile", PROFILE],
  ...MODEL_KEY_CAPABILITIES.map((name): [string, Target] => [name, MODEL_KEY]),
]);

// What to call a link to a page nothing more specific is known about.
function genericLabel(pathname: string): string {
  if (pathname === INTEGRATIONS) return "Open Integrations";
  if (pathname === "/profile") return "Open your profile";
  return "Continue";
}

function labelFor(pathname: string, capability: string | undefined, missing: readonly string[]): string {
  for (const token of missing) {
    const target = TARGET_BY_MISSING.get(token);
    if (target !== undefined && target.path === pathname) return target.label;
  }
  if (capability !== undefined && CAPABILITY_TOKEN.test(capability)) {
    const target = TARGET_BY_CAPABILITY.get(capability);
    if (target !== undefined && target.path === pathname) return target.label;
  }
  return genericLabel(pathname);
}

// ── the notice ─────────────────────────────────────────────────────────────

// `linkTo` and `linkLabel` are both set or both null.
export interface SetupNotice {
  message: string;
  linkTo: string | null;
  linkLabel: string | null;
}

// A failed request's value, shown as one thing: a notice or a plain message.
export type Problem = string | SetupNotice;

// The notice for a SETUP_REQUIRED error, or null for anything else (any other code, a
// bare Error such as a failed fetch, a value that is not an Error). It reads the fields
// `ApiError` carries -- `code`, `settingsPath`, `capability`, `missing` -- structurally.
export function setupRequiredNotice(error: unknown): SetupNotice | null {
  if (!(error instanceof Error)) return null;
  const fields = error as Error & {
    code?: unknown;
    settingsPath?: unknown;
    capability?: unknown;
    missing?: unknown;
  };
  if (fields.code !== SETUP_REQUIRED_CODE) return null;

  const message = error.message.trim() === "" ? GENERIC_SETUP_MESSAGE : error.message;
  const linkTo = safeSettingsLink(fields.settingsPath);
  if (linkTo === null) return { message, linkTo: null, linkLabel: null };

  const capability = typeof fields.capability === "string" ? fields.capability : undefined;
  const missing = Array.isArray(fields.missing)
    ? fields.missing.filter((item): item is string => typeof item === "string")
    : [];
  return { message, linkTo, linkLabel: labelFor(pathnameOf(linkTo), capability, missing) };
}

// ── a failure, as a component shows it ─────────────────────────────────────

// What a failed request becomes in a component's state: the setup notice (the server's
// sentence plus the link to the fix) or a plain error message. A component that holds a
// failure in its state sets it from `failureOf` and nothing else, so it cannot keep the
// notice and then draw only its sentence. A setup failure has no Retry beside it: asking
// again cannot succeed until the person has done what the notice says.
export type Failure =
  | { kind: "setup"; notice: SetupNotice }
  | { kind: "error"; message: string };

export function failureOf(error: unknown, fallback: string): Failure {
  const notice = setupRequiredNotice(error);
  if (notice !== null) return { kind: "setup", notice };
  return { kind: "error", message: friendlyApiMessage(error, fallback) };
}

// The same for the places that keep one slot for "what went wrong" (rendered by
// ProblemView): the notice, or the message, never both and never neither.
export function problemOf(error: unknown, fallback: string): Problem {
  return setupRequiredNotice(error) ?? friendlyApiMessage(error, fallback);
}
