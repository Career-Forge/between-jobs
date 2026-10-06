// The tester programme, as pure logic: what the API says about a person's
// enrollment, the form that joins them, the requests it makes, and the decision that sends a
// person who has not joined to the enrollment page when the server requires it.
//
// WHAT THE SERVER OWNS, AND WHAT THIS MODULE ONLY DRAWS. The server decides who counts as
// enrolled (consent to the CURRENT agreement, not withdrawn) and refuses the costly features to
// everyone else with 403 ENROLLMENT_REQUIRED when the operator has turned the programme on
// (src/between_jobs/api/tester_enrollment.py). The gate here is a courtesy: it shows the person
// the page that fixes it instead of letting them find out one failed request at a time. Nothing
// about it is a security boundary, so where it cannot tell it leans toward showing the page
// (it never hides a refusal the server would give), and where the server said nothing about the
// programme it leans toward getting out of the way.
//
// The three-state rule applies twice here. The sponsorship answer is yes, no or "prefer not to
// say" (stored as null, "not given", never read as "no"). And the lookup of one's own enrollment
// is loading, failed or known: a failed lookup is its own state with a retry, never read as
// "enrolled" (which would let a person through silently) or as "not enrolled" (which would lock
// them out without a word).
//
// Pure module: the fetcher is injected (api.ts pulls in the Supabase client, which throws at
// import time without env vars) and there is no React here. The wiring is
// components/EnrollmentGate.tsx and pages/Enroll.tsx.

import { TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import { isTesterProgramRequired, type CapabilitiesState } from "./capabilities";
import { isTimeoutError } from "./deadline";
import { UPDATE_PASSWORD_PATH } from "./passwordReset";
import {
  ENROLL_PAGE_NAME,
  ENROLL_PATH,
  PRIVACY_PATH,
  TERMS_PATH,
  documentTitleFor,
  normalizePath,
  pageTitle,
} from "./publicRoutes";
import { friendlyApiMessage } from "./rateLimitMessage";

export const ENROLLMENT_PATH = "/tester/enrollment";
export const WITHDRAW_PATH = "/tester/enrollment/withdraw";

export type EnrollmentFetcher = <T>(path: string, init?: RequestInit) => Promise<T>;

// ── the closed lists ───────────────────────────────────────────────────────

// The role cohorts and seniority bands the database allows (the CHECK constraints on
// tester_enrollments, mirrored by ROLE_COHORTS and SENIORITIES in tester_enrollment.py).
// enrollment.test.ts reads the migration and fails when either side changes alone.
export const ROLE_COHORTS = [
  { value: "data_analyst", label: "Data analyst" },
  { value: "data_engineer", label: "Data engineer" },
  { value: "data_scientist", label: "Data scientist" },
  { value: "ai_ml_engineer", label: "AI or machine learning engineer" },
  { value: "software_engineer", label: "Software engineer" },
  { value: "frontend_engineer", label: "Front-end engineer" },
  { value: "devops_sre", label: "DevOps or site reliability engineer" },
  { value: "qa_sdet", label: "QA or test automation engineer" },
  { value: "product_manager", label: "Product manager" },
  { value: "business_analyst", label: "Business analyst" },
] as const;

export const SENIORITIES = [
  { value: "new_grad", label: "New graduate" },
  { value: "early_career", label: "Early career" },
  { value: "mid", label: "Mid-level" },
  { value: "senior", label: "Senior" },
  { value: "lead_plus", label: "Lead or above" },
] as const;

export type RoleCohort = (typeof ROLE_COHORTS)[number]["value"];
export type Seniority = (typeof SENIORITIES)[number]["value"];

export function isRoleCohort(value: unknown): value is RoleCohort {
  return ROLE_COHORTS.some((option) => option.value === value);
}

export function isSeniority(value: unknown): value is Seniority {
  return SENIORITIES.some((option) => option.value === value);
}

export function roleCohortLabel(value: RoleCohort | null): string | null {
  return ROLE_COHORTS.find((option) => option.value === value)?.label ?? null;
}

export function seniorityLabel(value: Seniority | null): string | null {
  return SENIORITIES.find((option) => option.value === value)?.label ?? null;
}

// ── the sponsorship question: yes, no, or not given ────────────────────────

export type SponsorshipAnswer = "yes" | "no" | "not_given";

export const SPONSORSHIP_QUESTION = "Would you need an employer to sponsor your right to work?";

// One line, shown beside the question: the reason it is asked, and that it is optional.
export const SPONSORSHIP_REASON =
  "Optional. We ask only so that our reports can tell a product that fails people who need sponsorship apart from a job market that does. The product never uses your answer.";

export const SPONSORSHIP_OPTIONS: readonly { value: SponsorshipAnswer; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "not_given", label: "Prefer not to say" },
];

// What goes on the wire: "not given" is null, which the server stores as "not asked or declined".
export function sponsorshipToApi(answer: SponsorshipAnswer): boolean | null {
  if (answer === "yes") return true;
  if (answer === "no") return false;
  return null;
}

export function sponsorshipFromApi(value: boolean | null): SponsorshipAnswer {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "not_given";
}

// ── what the API says about one's own enrollment ───────────────────────────

export interface Enrollment {
  // Consent to the CURRENT agreement and not withdrawn: what the server's gate checks.
  enrolled: boolean;
  roleCohort: RoleCohort | null;
  seniority: Seniority | null;
  needsSponsorship: boolean | null;
  consentVersion: string | null;
  consentedAt: string | null;
  withdrawnAt: string | null;
  // The version the SERVER holds as current: when it is not the one this bundle shows, the page
  // is out of date and must be reloaded before anyone accepts anything.
  currentVersion: string | null;
  // A current tester (not withdrawn) whose accepted version is an older one.
  needsReconsent: boolean;
}

export type EnrollmentStatus = "not_joined" | "joined" | "withdrawn" | "needs_reconsent";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** The body as an `Enrollment`, or null if it is not one. A missing `enrolled` is not guessed
 *  at. A value this app does not know (a cohort a newer server added, a non-boolean sponsorship
 *  answer) is unknown, not a reason to reject the whole answer and not rendered as a known one. */
export function parseEnrollment(body: unknown): Enrollment | null {
  if (!isRecord(body) || typeof body.enrolled !== "boolean") return null;
  return {
    enrolled: body.enrolled,
    roleCohort: isRoleCohort(body.role_cohort) ? body.role_cohort : null,
    seniority: isSeniority(body.seniority) ? body.seniority : null,
    needsSponsorship: typeof body.needs_sponsorship === "boolean" ? body.needs_sponsorship : null,
    consentVersion: stringOrNull(body.consent_version),
    consentedAt: stringOrNull(body.consented_at),
    withdrawnAt: stringOrNull(body.withdrawn_at),
    currentVersion: stringOrNull(body.current_version),
    needsReconsent: body.needs_reconsent === true,
  };
}

export function enrollmentStatus(enrollment: Enrollment): EnrollmentStatus {
  if (enrollment.enrolled) return "joined";
  if (enrollment.withdrawnAt !== null) return "withdrawn";
  if (enrollment.needsReconsent) return "needs_reconsent";
  return "not_joined";
}

// How the server's current agreement stands against the one this bundle shows. The two ship
// separately (the API and the web bundle deploy on their own), so after a change to the agreement
// either side can be the one that has not caught up, and what to tell the person differs.
export type AgreementSkew =
  // The same version, or the server did not say (it sends none to someone with no record).
  | "none"
  // The server's is the newer: this page is out of date, and a reload fixes it.
  | "page_older"
  // This page's is the newer: the server has not been updated yet, and a reload cannot fix it.
  | "page_newer"
  // They differ and cannot be ordered (a version that is not a date): no direction is claimed.
  | "unknown";

const DATE_VERSION = /^\d{4}-\d{2}-\d{2}$/;

/** Compares the server's version with this bundle's. Both are dates (the web constant is pinned
 *  to that shape by testerAgreement.test.ts), which order as text; anything else is "unknown",
 *  never guessed into a direction. */
export function agreementSkew(enrollment: Enrollment | null): AgreementSkew {
  if (enrollment === null || enrollment.currentVersion === null) return "none";
  const server = enrollment.currentVersion;
  if (server === TESTER_AGREEMENT_VERSION) return "none";
  if (DATE_VERSION.test(server) && DATE_VERSION.test(TESTER_AGREEMENT_VERSION)) {
    return server > TESTER_AGREEMENT_VERSION ? "page_older" : "page_newer";
  }
  return "unknown";
}

/** True when the server's current agreement is not the one this bundle was built with, in either
 *  direction, so what is on screen is not what would be recorded (the server would refuse it). */
export function agreementVersionsDiffer(enrollment: Enrollment | null): boolean {
  return agreementSkew(enrollment) !== "none";
}

// "October 6, 2026", or null when the value is not a date. In UTC, so the same instant reads the
// same everywhere.
export function formatEnrollmentDate(iso: string | null): string | null {
  if (iso === null) return null;
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return null;
  return time.toLocaleDateString("en-US", {
    timeZone: "UTC",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

// ── looking it up: loading, failed, or known ───────────────────────────────

export type EnrollmentLoad =
  // Not asked yet.
  | { kind: "idle" }
  | { kind: "loading" }
  // Asking failed, or the answer was not the shape we expect. Never read as a yes or a no.
  | { kind: "failed"; message: string }
  | { kind: "ready"; enrollment: Enrollment };

export type EnrollmentLoadEvent =
  | { type: "load_started" }
  | { type: "loaded"; enrollment: Enrollment }
  | { type: "load_failed"; message: string }
  // What a join or a withdrawal answered with: the new truth.
  | { type: "replaced"; enrollment: Enrollment };

export const initialEnrollmentLoad: EnrollmentLoad = { kind: "idle" };

// A refresh of something already known keeps showing it: a started refresh changes nothing and a
// failed one does not take back what is known. Only an unknown state moves to loading or failed.
export function enrollmentLoadReducer(
  state: EnrollmentLoad,
  event: EnrollmentLoadEvent,
): EnrollmentLoad {
  switch (event.type) {
    case "load_started":
      return state.kind === "ready" ? state : { kind: "loading" };
    case "loaded":
    case "replaced":
      return { kind: "ready", enrollment: event.enrollment };
    case "load_failed":
      return state.kind === "ready" ? state : { kind: "failed", message: event.message };
  }
}

const LOOKUP_FAILED = "We could not check your tester enrollment.";
const LOOKUP_TIMED_OUT = "Checking your tester enrollment took too long.";

// How long the lookup may take before it counts as failed, which the gate shows as its retry
// state. Without it a server that hangs leaves the page blank until the browser gives up.
export const ENROLLMENT_LOOKUP_TIMEOUT_MS = 8000;

const READ_FAILED =
  "We could not read the server's answer about your tester enrollment. Reload the page to see where you stand.";

/** Asks where the person stands. Never throws: a failed ask is an event like any other. */
export async function loadEnrollment(fetcher: EnrollmentFetcher): Promise<EnrollmentLoadEvent> {
  try {
    const enrollment = parseEnrollment(await fetcher<unknown>(ENROLLMENT_PATH));
    return enrollment === null
      ? { type: "load_failed", message: READ_FAILED }
      : { type: "loaded", enrollment };
  } catch (e) {
    // A deadline that passed is not a message from the server, and its own text ("signal timed
    // out") says nothing to the person.
    return {
      type: "load_failed",
      message: isTimeoutError(e) ? LOOKUP_TIMED_OUT : friendlyApiMessage(e, LOOKUP_FAILED),
    };
  }
}

// ── the form ───────────────────────────────────────────────────────────────

export interface EnrollForm {
  roleCohort: RoleCohort | "";
  seniority: Seniority | "";
  sponsorship: SponsorshipAnswer;
  // The box that says the person has read the agreement, the Terms and the Privacy Policy.
  agreed: boolean;
}

// "Prefer not to say" is what is selected until the person chooses otherwise, so leaving the
// question alone sends nothing about their immigration situation.
export const emptyEnrollForm: EnrollForm = {
  roleCohort: "",
  seniority: "",
  sponsorship: "not_given",
  agreed: false,
};

/** The answers a person gave before (a withdrawn tester joining again, a tester accepting a
 *  newer agreement), so they are not asked to retype them. The box is never pre-ticked: consent
 *  is given each time, not carried over. */
export function formFromEnrollment(enrollment: Enrollment | null): EnrollForm {
  if (enrollment === null) return emptyEnrollForm;
  return {
    roleCohort: enrollment.roleCohort ?? "",
    seniority: enrollment.seniority ?? "",
    sponsorship: sponsorshipFromApi(enrollment.needsSponsorship),
    agreed: false,
  };
}

export interface EnrollFormErrors {
  roleCohort?: string;
  seniority?: string;
  agreed?: string;
}

export const AGREE_LABEL =
  "I have read and agree to the Tester Agreement, the Terms and the Privacy Policy";

export function validateEnrollForm(form: EnrollForm): EnrollFormErrors {
  const errors: EnrollFormErrors = {};
  if (form.roleCohort === "") errors.roleCohort = "Choose the role you are looking for.";
  if (form.seniority === "") errors.seniority = "Choose your seniority.";
  if (!form.agreed) {
    errors.agreed = "Tick the box to say you have read and agree to the three documents.";
  }
  return errors;
}

// The ids of the three controls that can be invalid, so that the page can move focus to the first
// one a failed submit names (EnrollmentView draws them with these same ids).
export const ENROLL_FIELD_IDS = {
  roleCohort: "bj-enroll-role",
  seniority: "bj-enroll-seniority",
  agreed: "bj-enroll-agreed",
} as const;

/** The id of the first control with an error, in the order the form reads, or null for none. */
export function firstInvalidFieldId(errors: EnrollFormErrors): string | null {
  if (errors.roleCohort !== undefined) return ENROLL_FIELD_IDS.roleCohort;
  if (errors.seniority !== undefined) return ENROLL_FIELD_IDS.seniority;
  if (errors.agreed !== undefined) return ENROLL_FIELD_IDS.agreed;
  return null;
}

// The three things the page moves focus to when the control the person just used goes away: the
// Withdraw button (back to it when they cancel), the confirmation (when they ask), and the banner
// that says what happened (after a join or a withdrawal). EnrollmentView draws them with these ids
// and tabIndex -1, so they can take focus without joining the tab order.
export const ENROLL_FOCUS_IDS = {
  withdraw: "bj-enroll-withdraw",
  confirm: "bj-enroll-confirm",
  notice: "bj-enroll-notice",
} as const;

export type FocusFacts = Pick<EnrollPageState, "confirmingWithdraw" | "notice">;

/** Where focus should go after the page changed from `before` to `after`, or null to leave it.
 *  A button that is activated and then unmounted (the Withdraw button, "Yes, withdraw", the join
 *  button) drops focus to the document, so a keyboard or screen-reader user would have to start
 *  again from the top of the page. In order: a new result banner (a join, a withdrawal) takes it;
 *  asking to withdraw moves it to the confirmation, not to the destructive button; cancelling
 *  returns it to the Withdraw button. A failure is not here: the error banner takes focus itself. */
export function focusTargetAfterChange(before: FocusFacts, after: FocusFacts): string | null {
  if (after.notice !== null && after.notice !== before.notice) return ENROLL_FOCUS_IDS.notice;
  if (after.confirmingWithdraw && !before.confirmingWithdraw) return ENROLL_FOCUS_IDS.confirm;
  if (!after.confirmingWithdraw && before.confirmingWithdraw && after.notice === null) {
    return ENROLL_FOCUS_IDS.withdraw;
  }
  return null;
}

export function isEnrollFormValid(form: EnrollForm): boolean {
  return Object.keys(validateEnrollForm(form)).length === 0;
}

export interface EnrollRequest {
  role_cohort: RoleCohort;
  seniority: Seniority;
  needs_sponsorship: boolean | null;
  accept_version: string;
}

/** The body of POST /tester/enrollment, or null when the form is not complete. The version
 *  accepted is the one this bundle shows: the server refuses it (409) if that is no longer the
 *  current one. */
export function buildEnrollRequest(form: EnrollForm): EnrollRequest | null {
  if (!isEnrollFormValid(form) || form.roleCohort === "" || form.seniority === "") return null;
  return {
    role_cohort: form.roleCohort,
    seniority: form.seniority,
    needs_sponsorship: sponsorshipToApi(form.sponsorship),
    accept_version: TESTER_AGREEMENT_VERSION,
  };
}

// ── joining and withdrawing ────────────────────────────────────────────────

export type EnrollmentOutcome = { ok: true; enrollment: Enrollment } | { ok: false; message: string };

// Either side may be the one that is behind (see AgreementSkew), so this claims no direction.
export const AGREEMENT_CHANGED_MESSAGE =
  "The tester agreement on this page and the one on the server are not the same version. Nothing was recorded. Reload the page and read what it shows before you accept; if it keeps happening, try again in a few minutes.";

// Read structurally, so this module needs no import of ApiError (which would pull in the
// Supabase client).
function codeOf(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null || !("code" in error)) return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

/** Whether a failed request was the server refusing a costly feature because the person has not
 *  joined the programme (403 ENROLLMENT_REQUIRED). api.ts tells the shell's gate when it sees one,
 *  so the gate can ask again where the person stands. */
export function isEnrollmentRefusal(error: unknown): boolean {
  return codeOf(error) === "ENROLLMENT_REQUIRED";
}

/** The text for a failed join or withdrawal: a mismatched agreement (409 CONFLICT) says to
 *  reload, and anything else goes through the shared wording (rateLimitMessage.ts: a rate limit
 *  says when to try again, a refusal for enrollment says to open the enrollment page, anything
 *  else keeps the server's own message). The enrollment routes are never themselves refused for
 *  enrollment, so that case has no sentence of its own here to drift from the shared one. */
export function enrollmentErrorMessage(error: unknown, fallback: string): string {
  if (codeOf(error) === "CONFLICT") return AGREEMENT_CHANGED_MESSAGE;
  return friendlyApiMessage(error, fallback);
}

const JOIN_FAILED = "We could not record your enrollment. Try again in a moment.";
const WITHDRAW_FAILED = "We could not record your withdrawal. Try again in a moment.";
const INCOMPLETE_FORM = "Choose your role and seniority and tick the box first.";

/** Sends the form. A form that is not complete is never sent. */
export async function submitEnrollment(
  fetcher: EnrollmentFetcher,
  form: EnrollForm,
): Promise<EnrollmentOutcome> {
  const request = buildEnrollRequest(form);
  if (request === null) return { ok: false, message: INCOMPLETE_FORM };
  try {
    const enrollment = parseEnrollment(
      await fetcher<unknown>(ENROLLMENT_PATH, { method: "POST", body: JSON.stringify(request) }),
    );
    if (enrollment === null) return { ok: false, message: READ_FAILED };
    // A 200 that does not say enrolled is not a success to announce.
    if (!enrollment.enrolled) return { ok: false, message: JOIN_FAILED };
    return { ok: true, enrollment };
  } catch (e) {
    return { ok: false, message: enrollmentErrorMessage(e, JOIN_FAILED) };
  }
}

export async function withdrawEnrollment(fetcher: EnrollmentFetcher): Promise<EnrollmentOutcome> {
  try {
    const enrollment = parseEnrollment(await fetcher<unknown>(WITHDRAW_PATH, { method: "POST" }));
    if (enrollment === null) return { ok: false, message: READ_FAILED };
    return { ok: true, enrollment };
  } catch (e) {
    return { ok: false, message: enrollmentErrorMessage(e, WITHDRAW_FAILED) };
  }
}

// ── the enrollment page's own state ────────────────────────────────────────

export interface EnrollPageState {
  form: EnrollForm;
  // Whether the form has been filled from an earlier enrollment (it is, once).
  seeded: boolean;
  // Field errors show only after the person has tried to submit.
  showErrors: boolean;
  // How many times they have tried to submit an incomplete form: each one moves focus to the
  // first field that needs attention, so the count, not the flag, is what the page watches.
  attempts: number;
  busy: "idle" | "joining" | "withdrawing";
  error: string | null;
  confirmingWithdraw: boolean;
  notice: "joined" | "withdrew" | null;
}

export const initialEnrollPageState: EnrollPageState = {
  form: emptyEnrollForm,
  seeded: false,
  showErrors: false,
  attempts: 0,
  busy: "idle",
  error: null,
  confirmingWithdraw: false,
  notice: null,
};

export type EnrollPageEvent =
  | { type: "seed"; enrollment: Enrollment }
  | { type: "role"; value: string }
  | { type: "seniority"; value: string }
  | { type: "sponsorship"; value: SponsorshipAnswer }
  | { type: "agreed"; value: boolean }
  | { type: "submit_attempted" }
  | { type: "join_started" }
  | { type: "join_failed"; message: string }
  | { type: "joined" }
  | { type: "withdraw_asked" }
  | { type: "withdraw_cancelled" }
  | { type: "withdraw_started" }
  | { type: "withdraw_failed"; message: string }
  | { type: "withdrew" };

export function enrollPageReducer(state: EnrollPageState, event: EnrollPageEvent): EnrollPageState {
  switch (event.type) {
    case "seed":
      return state.seeded ? state : { ...state, seeded: true, form: formFromEnrollment(event.enrollment) };
    case "role":
      return { ...state, form: { ...state.form, roleCohort: isRoleCohort(event.value) ? event.value : "" } };
    case "seniority":
      return { ...state, form: { ...state.form, seniority: isSeniority(event.value) ? event.value : "" } };
    case "sponsorship":
      return { ...state, form: { ...state.form, sponsorship: event.value } };
    case "agreed":
      return { ...state, form: { ...state.form, agreed: event.value } };
    case "submit_attempted":
      return { ...state, showErrors: true, attempts: state.attempts + 1, error: null };
    case "join_started":
      return { ...state, busy: "joining", error: null, notice: null };
    case "join_failed":
      return { ...state, busy: "idle", error: event.message };
    case "joined":
      return {
        ...state,
        busy: "idle",
        error: null,
        showErrors: false,
        notice: "joined",
        form: { ...state.form, agreed: false },
      };
    case "withdraw_asked":
      return { ...state, confirmingWithdraw: true, error: null, notice: null };
    case "withdraw_cancelled":
      return { ...state, confirmingWithdraw: false };
    case "withdraw_started":
      return { ...state, busy: "withdrawing", error: null };
    case "withdraw_failed":
      return { ...state, busy: "idle", error: event.message };
    case "withdrew":
      return {
        ...state,
        busy: "idle",
        confirmingWithdraw: false,
        error: null,
        notice: "withdrew",
        form: { ...state.form, agreed: false },
      };
  }
}

// ── the gate: who is sent to the enrollment page ───────────────────────────

// The pages a person who has not joined can still reach when the programme is required. The
// server leaves everything cheap open (tester_enrollment.py: "nobody is locked out of leaving"),
// and the gate here must be no stricter than the server for anything that switches something off
// or leaves:
//   - the enrollment page itself, and the two legal documents they are asked to agree to;
//   - Profile, where account deletion lives;
//   - Integrations, where a provider key is removed, Gmail is disconnected, and a saved search is
//     paused or deleted. Gmail reply checking and saved searches run in the background on the
//     person's own key whether or not they are enrolled, so a person who withdrew (or never
//     joined, on a server that switched the programme on later) must be able to reach the
//     controls that stop them. Saving a key there is still refused by the server, which the page
//     already shows as an error;
//   - the new-password page: a person who followed an emailed reset link is sent there from every
//     address (passwordReset.ts), has a session that sign-out would lose, and can do nothing else
//     until the password is set, so consent to a programme is never a condition of recovery.
export const PROFILE_PATH = "/profile";
export const INTEGRATIONS_PATH = "/profile/integrations";
export const GATE_EXEMPT_PATHS: readonly string[] = [
  ENROLL_PATH,
  PRIVACY_PATH,
  TERMS_PATH,
  PROFILE_PATH,
  INTEGRATIONS_PATH,
  UPDATE_PASSWORD_PATH,
];

/** Whether this address is one the gate never stands in front of. Matched exactly, like every
 *  path in the app (publicRoutes.ts): "/profile/" is "/profile", while "/Profile" and
 *  "/profile/integrations/extra" are not, and a spelling the gate does not recognise is gated,
 *  which is the safe direction. */
export function isGateExempt(pathname: string): boolean {
  return GATE_EXEMPT_PATHS.includes(normalizePath(pathname));
}

export type GateDecision =
  // Draw the page the person asked for.
  | "pass"
  // Not enough is known to decide: draw nothing (a flash of the app, taken back, is worse).
  | "wait"
  // Draw the enrollment page in its place.
  | "enroll"
  // The lookup failed: say so, and offer to try again.
  | "retry";

export interface GateInput {
  capabilities: CapabilitiesState;
  enrollment: EnrollmentLoad;
  pathname: string;
}

/** What the signed-in shell draws for this address. In order:
 *   - the exempt pages always pass;
 *   - while the server's settings are unknown: wait. Once they are: a server that does not
 *     require the programme, or that could not be asked, changes nothing for anyone (it still
 *     refuses what it must, with a message that says to join);
 *   - a server that requires it: wait for the lookup, retry if it failed, and send the person
 *     to the enrollment page unless they are enrolled. */
export function enrollmentGate(input: GateInput): GateDecision {
  if (isGateExempt(input.pathname)) return "pass";
  if (input.capabilities.kind === "checking") return "wait";
  if (!isTesterProgramRequired(input.capabilities)) return "pass";
  switch (input.enrollment.kind) {
    case "idle":
    case "loading":
      return "wait";
    case "failed":
      return "retry";
    case "ready":
      return input.enrollment.enrollment.enrolled ? "pass" : "enroll";
  }
}

/** The document title for what the gate draws. The shell titles a page from its address, but the
 *  gate can draw the enrollment page (or its retry) at ANY address, and then the tab, the history
 *  entry and a screen reader's page title would say "Between Jobs" over a page headed "Tester
 *  programme". So the gate, which alone knows what it drew, owns the title: the enrollment page's
 *  while it stands in for another, and the address's own otherwise. */
export function enrollmentTitleFor(decision: GateDecision, pathname: string): string {
  if (decision === "enroll" || decision === "retry") return pageTitle(ENROLL_PAGE_NAME);
  return documentTitleFor("app", pathname) ?? pageTitle(null);
}

/** Whether the gate needs the person's enrollment and has not asked for it yet. */
export function shouldLoadEnrollment(
  capabilities: CapabilitiesState,
  enrollment: EnrollmentLoad,
): boolean {
  return isTesterProgramRequired(capabilities) && enrollment.kind === "idle";
}

// ── the line on the Profile page ───────────────────────────────────────────

export const PROFILE_LINE_HEADING = "Tester programme";
export const PROFILE_LINE_LINK = "Join or withdraw";

/** The sentence under the Profile page's "Tester programme" heading, or null when the line is
 *  not shown. It is shown only when the server requires the programme, or the person is already
 *  in it (or on an older agreement): a visitor to a server that has no programme is never
 *  offered one. While the person's own state is not known the line says only what is certain. */
export function profileLineStatus(
  capabilities: CapabilitiesState,
  enrollment: EnrollmentLoad,
): string | null {
  const required = isTesterProgramRequired(capabilities);
  if (enrollment.kind !== "ready") {
    return required ? "Joining the tester programme is required on this server." : null;
  }
  const current = enrollment.enrollment;
  switch (enrollmentStatus(current)) {
    case "joined":
      return "You are in the tester programme.";
    case "needs_reconsent":
      return "The tester agreement has changed since you accepted it. Read it again and accept the new version.";
    case "withdrawn": {
      if (!required) return null;
      const when = formatEnrollmentDate(current.withdrawnAt);
      return when === null
        ? "You withdrew from the tester programme."
        : `You withdrew from the tester programme on ${when}.`;
    }
    case "not_joined":
      return required ? "You have not joined the tester programme." : null;
  }
}
