import { describe, expect, it } from "vitest";
import { TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import { ApiError } from "./api";
import type { CapabilitiesState } from "./capabilities";
import { UPDATE_PASSWORD_PATH, recoveryRedirect } from "./passwordReset";
import { ENROLL_PAGE_NAME, documentTitleFor } from "./publicRoutes";
import { ENROLLMENT_REQUIRED_MESSAGE } from "./rateLimitMessage";
import {
  AGREEMENT_CHANGED_MESSAGE,
  ENROLLMENT_LOOKUP_TIMEOUT_MS,
  ENROLLMENT_PATH,
  ENROLL_FOCUS_IDS,
  GATE_EXEMPT_PATHS,
  INTEGRATIONS_PATH,
  ROLE_COHORTS,
  SENIORITIES,
  SPONSORSHIP_OPTIONS,
  WITHDRAW_PATH,
  agreementSkew,
  agreementVersionsDiffer,
  buildEnrollRequest,
  emptyEnrollForm,
  enrollPageReducer,
  enrollmentErrorMessage,
  enrollmentGate,
  enrollmentTitleFor,
  ENROLL_FIELD_IDS,
  firstInvalidFieldId,
  focusTargetAfterChange,
  enrollmentLoadReducer,
  enrollmentStatus,
  formFromEnrollment,
  formatEnrollmentDate,
  initialEnrollPageState,
  initialEnrollmentLoad,
  isEnrollFormValid,
  isEnrollmentRefusal,
  isGateExempt,
  isRoleCohort,
  isSeniority,
  loadEnrollment,
  parseEnrollment,
  profileLineStatus,
  roleCohortLabel,
  seniorityLabel,
  shouldLoadEnrollment,
  sponsorshipFromApi,
  sponsorshipToApi,
  submitEnrollment,
  validateEnrollForm,
  withdrawEnrollment,
  type EnrollForm,
  type Enrollment,
  type EnrollmentFetcher,
  type EnrollmentLoad,
  type GateDecision,
} from "./enrollment";

// The pure half of the tester programme: the lists it shares with the database, what the API's
// answer is read as, the form and its request, joining and withdrawing, the page's state, and the
// decision that sends a person who has not joined to the enrollment page. The wiring that draws
// it is components/EnrollmentGate.tsx and pages/Enroll.tsx (enrollmentWiring.test.tsx).

const migrations = import.meta.glob("../../../supabase/migrations/*.sql", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const ENROLLMENT_MIGRATION =
  Object.entries(migrations).find(([path]) =>
    path.endsWith("_create_product_events_and_tester_enrollments.sql"),
  )?.[1] ?? "";

// "in ('a', 'b', ...)" after a column name inside the tester_enrollments table, as a list.
function checkList(column: string): string[] {
  const table = ENROLLMENT_MIGRATION.split("create table public.tester_enrollments")[1].split(
    "comment on column",
  )[0];
  const match = new RegExp(`${column}\\s+text\\s+not\\s+null\\s+check\\s*\\(\\s*${column}\\s+in\\s*\\(([^)]*)\\)`).exec(
    table.replace(/--[^\n]*/g, ""),
  );
  if (match === null) throw new Error(`no CHECK list for ${column}`);
  return [...match[1].matchAll(/'([^']*)'/g)].map((m) => m[1]);
}

function enrollment(overrides: Partial<Enrollment> = {}): Enrollment {
  return {
    enrolled: true,
    roleCohort: "data_analyst",
    seniority: "mid",
    needsSponsorship: null,
    consentVersion: TESTER_AGREEMENT_VERSION,
    consentedAt: "2026-10-06T09:00:00+00:00",
    withdrawnAt: null,
    currentVersion: TESTER_AGREEMENT_VERSION,
    needsReconsent: false,
    ...overrides,
  };
}

const NOT_ENROLLED: Enrollment = enrollment({
  enrolled: false,
  roleCohort: null,
  seniority: null,
  consentVersion: null,
  consentedAt: null,
  currentVersion: null,
});

const FILLED: EnrollForm = {
  roleCohort: "software_engineer",
  seniority: "senior",
  sponsorship: "no",
  agreed: true,
};

// ── the lists ──────────────────────────────────────────────────────────────

describe("the role cohorts and seniority bands", () => {
  it("are the ones the database allows, in the same words", () => {
    expect(ROLE_COHORTS.map((option) => option.value)).toEqual(checkList("role_cohort"));
    expect(SENIORITIES.map((option) => option.value)).toEqual(checkList("seniority"));
  });

  it("each have a plain label of their own", () => {
    for (const options of [ROLE_COHORTS, SENIORITIES]) {
      const labels = options.map((option) => option.label);
      expect(new Set(labels).size).toBe(labels.length);
      for (const option of options) {
        expect(option.label.trim().length).toBeGreaterThan(0);
        // not the database's own spelling, which has underscores and abbreviations
        expect(option.label).not.toContain("_");
      }
    }
  });

  it("are looked up by value, and nothing else is a value", () => {
    expect(isRoleCohort("devops_sre")).toBe(true);
    expect(isRoleCohort("astronaut")).toBe(false);
    expect(isRoleCohort("")).toBe(false);
    expect(isRoleCohort(null)).toBe(false);
    expect(isSeniority("lead_plus")).toBe(true);
    expect(isSeniority("Senior")).toBe(false);
    expect(roleCohortLabel("data_analyst")).toBe("Data analyst");
    expect(roleCohortLabel(null)).toBeNull();
    expect(seniorityLabel("new_grad")).toBe("New graduate");
    expect(seniorityLabel(null)).toBeNull();
  });

  it("use the words the agreement uses for the bands", () => {
    expect(SENIORITIES.map((option) => option.label.toLowerCase())).toEqual([
      "new graduate",
      "early career",
      "mid-level",
      "senior",
      "lead or above",
    ]);
  });
});

// ── the sponsorship question ───────────────────────────────────────────────

describe("the sponsorship answer", () => {
  it("is yes, no or prefer-not-to-say, and the last is sent as null", () => {
    expect(sponsorshipToApi("yes")).toBe(true);
    expect(sponsorshipToApi("no")).toBe(false);
    expect(sponsorshipToApi("not_given")).toBeNull();
  });

  it("comes back the same way, and null is 'not given', never 'no'", () => {
    expect(sponsorshipFromApi(true)).toBe("yes");
    expect(sponsorshipFromApi(false)).toBe("no");
    expect(sponsorshipFromApi(null)).toBe("not_given");
  });

  it("offers three radio options, the third being 'Prefer not to say'", () => {
    expect(SPONSORSHIP_OPTIONS.map((option) => option.label)).toEqual(["Yes", "No", "Prefer not to say"]);
  });

  it("starts as 'prefer not to say', so leaving it alone says nothing", () => {
    expect(emptyEnrollForm.sponsorship).toBe("not_given");
  });
});

// ── reading the API's answer ───────────────────────────────────────────────

describe("parseEnrollment", () => {
  it("reads a current tester", () => {
    expect(
      parseEnrollment({
        enrolled: true,
        role_cohort: "data_analyst",
        seniority: "mid",
        needs_sponsorship: false,
        consent_version: TESTER_AGREEMENT_VERSION,
        consented_at: "2026-10-06T09:00:00+00:00",
        withdrawn_at: null,
        current_version: TESTER_AGREEMENT_VERSION,
        needs_reconsent: false,
      }),
    ).toEqual(enrollment({ needsSponsorship: false }));
  });

  it("reads 'no row at all' as not enrolled, with nothing known", () => {
    expect(parseEnrollment({ enrolled: false })).toEqual({
      enrolled: false,
      roleCohort: null,
      seniority: null,
      needsSponsorship: null,
      consentVersion: null,
      consentedAt: null,
      withdrawnAt: null,
      currentVersion: null,
      needsReconsent: false,
    });
  });

  it("reads a withdrawn tester and one on an older agreement", () => {
    const withdrawn = parseEnrollment({
      enrolled: false,
      role_cohort: "qa_sdet",
      seniority: "senior",
      needs_sponsorship: true,
      consent_version: TESTER_AGREEMENT_VERSION,
      consented_at: "2026-10-06T09:00:00+00:00",
      withdrawn_at: "2026-10-07T09:00:00+00:00",
      current_version: TESTER_AGREEMENT_VERSION,
      needs_reconsent: false,
    });
    expect(withdrawn?.withdrawnAt).toBe("2026-10-07T09:00:00+00:00");
    expect(withdrawn && enrollmentStatus(withdrawn)).toBe("withdrawn");
    const older = parseEnrollment({ enrolled: false, consent_version: "2026-01-01", needs_reconsent: true });
    expect(older?.needsReconsent).toBe(true);
    expect(older && enrollmentStatus(older)).toBe("needs_reconsent");
  });

  it("treats a value it does not know as unknown, not as a reason to throw the answer away", () => {
    const parsed = parseEnrollment({
      enrolled: true,
      role_cohort: "astronaut",
      seniority: 7,
      needs_sponsorship: "maybe",
      consent_version: "",
      consented_at: 12,
      needs_reconsent: "yes",
    });
    expect(parsed).toEqual(
      enrollment({
        roleCohort: null,
        seniority: null,
        needsSponsorship: null,
        consentVersion: null,
        consentedAt: null,
        currentVersion: null,
        needsReconsent: false,
      }),
    );
  });

  it.each([
    ["null", null],
    ["a string", "enrolled"],
    ["an array", [{ enrolled: true }]],
    ["an empty object", {}],
    ["a string flag", { enrolled: "true" }],
    ["a numeric flag", { enrolled: 1 }],
    ["a null flag", { enrolled: null }],
  ])("does not guess from %s", (_label, body) => {
    expect(parseEnrollment(body)).toBeNull();
  });
});

describe("enrollmentStatus", () => {
  it("tells the four situations apart", () => {
    expect(enrollmentStatus(enrollment())).toBe("joined");
    expect(enrollmentStatus(NOT_ENROLLED)).toBe("not_joined");
    expect(enrollmentStatus(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" }))).toBe("withdrawn");
    expect(enrollmentStatus(enrollment({ enrolled: false, needsReconsent: true }))).toBe("needs_reconsent");
  });

  it("calls a withdrawn tester withdrawn even when the agreement has also changed", () => {
    expect(
      enrollmentStatus(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z", needsReconsent: true })),
    ).toBe("withdrawn");
  });
});

describe("agreementSkew and agreementVersionsDiffer", () => {
  // The API and the web bundle deploy separately, so after a change to the agreement either one can
  // be the side that has not caught up, and what the page says (and whether a reload helps) differs.
  it("is none when the versions are the same, or the server named none (it sends none to someone with no record)", () => {
    expect(agreementSkew(enrollment())).toBe("none");
    expect(agreementSkew(NOT_ENROLLED)).toBe("none");
    expect(agreementSkew(null)).toBe("none");
    expect(agreementVersionsDiffer(enrollment())).toBe(false);
    expect(agreementVersionsDiffer(NOT_ENROLLED)).toBe(false);
    expect(agreementVersionsDiffer(null)).toBe(false);
  });

  it("says the PAGE is the older one when the server's version is later (a reload fixes that)", () => {
    expect(agreementSkew(enrollment({ currentVersion: "2030-01-01" }))).toBe("page_older");
    // joined or not makes no difference to what the page shows
    expect(agreementSkew(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z", currentVersion: "2030-01-01" }))).toBe(
      "page_older",
    );
  });

  it("says the SERVER is the older one when its version is earlier (a reload cannot fix that: the API has not been updated yet)", () => {
    expect(agreementSkew(enrollment({ currentVersion: "2026-09-01" }))).toBe("page_newer");
    expect(agreementSkew(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z", currentVersion: "2026-09-01" }))).toBe(
      "page_newer",
    );
  });

  it("claims no direction for a version it cannot order", () => {
    for (const odd of ["v2", "2026-10", "2026-10-06T00:00:00Z", "later", "2026-1-6"]) {
      expect(agreementSkew(enrollment({ currentVersion: odd })), odd).toBe("unknown");
    }
  });

  it("differs in every case but none, because the server would refuse what the page would send", () => {
    for (const currentVersion of ["2030-01-01", "2026-09-01", "v2"]) {
      expect(agreementVersionsDiffer(enrollment({ currentVersion })), currentVersion).toBe(true);
    }
  });
});

describe("formatEnrollmentDate", () => {
  it("writes the day, in UTC", () => {
    expect(formatEnrollmentDate("2026-10-06T23:59:59+00:00")).toBe("October 6, 2026");
  });

  it("is null for no date and for text that is not one", () => {
    expect(formatEnrollmentDate(null)).toBeNull();
    expect(formatEnrollmentDate("soon")).toBeNull();
  });
});

// ── looking it up ──────────────────────────────────────────────────────────

describe("enrollmentLoadReducer", () => {
  const known: EnrollmentLoad = { kind: "ready", enrollment: enrollment() };

  it("goes idle, loading, ready", () => {
    let state = initialEnrollmentLoad;
    expect(state).toEqual({ kind: "idle" });
    state = enrollmentLoadReducer(state, { type: "load_started" });
    expect(state).toEqual({ kind: "loading" });
    state = enrollmentLoadReducer(state, { type: "loaded", enrollment: enrollment() });
    expect(state).toEqual(known);
  });

  it("goes loading to failed, and a retry goes failed to loading", () => {
    let state = enrollmentLoadReducer({ kind: "loading" }, { type: "load_failed", message: "down" });
    expect(state).toEqual({ kind: "failed", message: "down" });
    state = enrollmentLoadReducer(state, { type: "load_started" });
    expect(state).toEqual({ kind: "loading" });
  });

  it("keeps what is known through a refresh that starts or fails", () => {
    expect(enrollmentLoadReducer(known, { type: "load_started" })).toBe(known);
    expect(enrollmentLoadReducer(known, { type: "load_failed", message: "down" })).toBe(known);
  });

  it("takes a fresh answer over a stale 'enrolled' one, and the gate then sends the person to the enrollment page", () => {
    // A tab that opened enrolled, then the person withdrew elsewhere (or the agreement moved on).
    // A refusal from the server makes the shell ask again (api.ts, the gate): the answer replaces
    // the old one, and the same decision that let every page through now says enroll.
    const stale: EnrollmentLoad = { kind: "ready", enrollment: enrollment() };
    const required = caps(true);
    expect(enrollmentGate({ capabilities: required, enrollment: stale, pathname: "/discover" })).toBe("pass");

    for (const fresh of [
      enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" }),
      enrollment({ enrolled: false, needsReconsent: true, consentVersion: "2026-01-01" }),
      NOT_ENROLLED,
    ]) {
      const asking = enrollmentLoadReducer(stale, { type: "load_started" });
      expect(asking).toBe(stale); // while it is out, what was known stays
      const now = enrollmentLoadReducer(asking, { type: "loaded", enrollment: fresh });
      expect(enrollmentGate({ capabilities: required, enrollment: now, pathname: "/discover" })).toBe("enroll");
    }
  });

  it("takes an answer from a join or a withdrawal as the new truth", () => {
    expect(enrollmentLoadReducer({ kind: "failed", message: "x" }, { type: "replaced", enrollment: NOT_ENROLLED })).toEqual({
      kind: "ready",
      enrollment: NOT_ENROLLED,
    });
  });
});

describe("loadEnrollment", () => {
  it("asks the enrollment route and returns what it says", async () => {
    const asked: string[] = [];
    const fetcher: EnrollmentFetcher = async <T,>(path: string) => {
      asked.push(path);
      return { enrolled: false } as T;
    };
    expect(await loadEnrollment(fetcher)).toEqual({ type: "loaded", enrollment: NOT_ENROLLED });
    expect(asked).toEqual([ENROLLMENT_PATH]);
  });

  it("fails, with the server's own words, when the request fails", async () => {
    const fetcher: EnrollmentFetcher = async () => {
      throw new ApiError(503, "The service is down.", "PROVIDER_UNAVAILABLE", true);
    };
    expect(await loadEnrollment(fetcher)).toEqual({ type: "load_failed", message: "The service is down." });
  });

  it("fails, saying so, when it is a rate limit or something that is not an Error", async () => {
    const limited: EnrollmentFetcher = async () => {
      throw new ApiError(429, "x", "RATE_LIMITED", true, 700);
    };
    const event = await loadEnrollment(limited);
    expect(event.type === "load_failed" && event.message).toContain("too often");
    const odd: EnrollmentFetcher = async () => {
      throw "nope";
    };
    expect(await loadEnrollment(odd)).toEqual({
      type: "load_failed",
      message: "We could not check your tester enrollment.",
    });
  });

  it("fails when the answer is not the shape it expects, and never reads it as a yes or a no", async () => {
    const fetcher: EnrollmentFetcher = async <T,>() => ({ enrolled: "yes" }) as T;
    const event = await loadEnrollment(fetcher);
    expect(event.type).toBe("load_failed");
  });

  it("says it took too long when the deadline passed, not the browser's own words for it", async () => {
    const timedOut: EnrollmentFetcher = async () => {
      throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
    };
    expect(await loadEnrollment(timedOut)).toEqual({
      type: "load_failed",
      message: "Checking your tester enrollment took too long.",
    });
    const aborted: EnrollmentFetcher = async () => {
      throw new DOMException("signal is aborted without reason", "AbortError");
    };
    const event = await loadEnrollment(aborted);
    expect(event.type === "load_failed" && event.message).toBe("Checking your tester enrollment took too long.");
  });

  it("has a deadline of its own: the gate draws nothing while it is out", () => {
    expect(ENROLLMENT_LOOKUP_TIMEOUT_MS).toBeGreaterThan(0);
    expect(ENROLLMENT_LOOKUP_TIMEOUT_MS).toBeLessThanOrEqual(15_000);
  });
});

// ── the form ───────────────────────────────────────────────────────────────

describe("validateEnrollForm", () => {
  it("asks for a role, a seniority and the box, each in its own words", () => {
    const errors = validateEnrollForm(emptyEnrollForm);
    expect(errors.roleCohort).toContain("role");
    expect(errors.seniority).toContain("seniority");
    expect(errors.agreed).toContain("Tick the box");
  });

  it("is satisfied by a role, a seniority and the box, whatever the sponsorship answer", () => {
    for (const sponsorship of ["yes", "no", "not_given"] as const) {
      expect(isEnrollFormValid({ ...FILLED, sponsorship })).toBe(true);
    }
    expect(validateEnrollForm(FILLED)).toEqual({});
  });

  it("is not satisfied while any one of the three is missing", () => {
    expect(isEnrollFormValid({ ...FILLED, roleCohort: "" })).toBe(false);
    expect(isEnrollFormValid({ ...FILLED, seniority: "" })).toBe(false);
    expect(isEnrollFormValid({ ...FILLED, agreed: false })).toBe(false);
  });
});

describe("firstInvalidFieldId", () => {
  it("names the first control that needs attention, in the order the form reads", () => {
    expect(firstInvalidFieldId(validateEnrollForm(emptyEnrollForm))).toBe(ENROLL_FIELD_IDS.roleCohort);
    expect(firstInvalidFieldId(validateEnrollForm({ ...emptyEnrollForm, roleCohort: "qa_sdet" }))).toBe(
      ENROLL_FIELD_IDS.seniority,
    );
    expect(
      firstInvalidFieldId(validateEnrollForm({ ...emptyEnrollForm, roleCohort: "qa_sdet", seniority: "mid" })),
    ).toBe(ENROLL_FIELD_IDS.agreed);
  });

  it("names none for a form that is complete", () => {
    expect(firstInvalidFieldId(validateEnrollForm(FILLED))).toBeNull();
  });

  it("uses ids that are the controls' own", () => {
    expect(Object.values(ENROLL_FIELD_IDS)).toEqual(["bj-enroll-role", "bj-enroll-seniority", "bj-enroll-agreed"]);
  });
});

describe("buildEnrollRequest", () => {
  it("sends the answers, the sponsorship answer in its wire form, and the version this page shows", () => {
    expect(buildEnrollRequest(FILLED)).toEqual({
      role_cohort: "software_engineer",
      seniority: "senior",
      needs_sponsorship: false,
      accept_version: TESTER_AGREEMENT_VERSION,
    });
    expect(buildEnrollRequest({ ...FILLED, sponsorship: "yes" })?.needs_sponsorship).toBe(true);
    expect(buildEnrollRequest({ ...FILLED, sponsorship: "not_given" })?.needs_sponsorship).toBeNull();
  });

  it("takes the version from the content constant, which the server compares against its own", () => {
    expect(buildEnrollRequest(FILLED)?.accept_version).toBe(TESTER_AGREEMENT_VERSION);
    expect(TESTER_AGREEMENT_VERSION).toBe("2026-10-06");
  });

  it("builds nothing from a form that is not complete", () => {
    expect(buildEnrollRequest(emptyEnrollForm)).toBeNull();
    expect(buildEnrollRequest({ ...FILLED, agreed: false })).toBeNull();
    expect(buildEnrollRequest({ ...FILLED, roleCohort: "" })).toBeNull();
  });
});

describe("formFromEnrollment", () => {
  it("starts empty for someone who has never joined", () => {
    expect(formFromEnrollment(null)).toEqual(emptyEnrollForm);
    expect(formFromEnrollment(NOT_ENROLLED)).toEqual(emptyEnrollForm);
  });

  it("brings back an earlier tester's answers, but never the consent", () => {
    const form = formFromEnrollment(
      enrollment({ enrolled: false, roleCohort: "qa_sdet", seniority: "lead_plus", needsSponsorship: true, withdrawnAt: "2026-10-07T00:00:00Z" }),
    );
    expect(form).toEqual({ roleCohort: "qa_sdet", seniority: "lead_plus", sponsorship: "yes", agreed: false });
  });
});

// ── joining and withdrawing ────────────────────────────────────────────────

function recorder(answer: unknown) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetcher: EnrollmentFetcher = async <T,>(path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (answer instanceof Error) throw answer;
    return answer as T;
  };
  return { calls, fetcher };
}

const JOINED_BODY = {
  enrolled: true,
  role_cohort: "software_engineer",
  seniority: "senior",
  needs_sponsorship: false,
  consent_version: TESTER_AGREEMENT_VERSION,
  consented_at: "2026-10-06T09:00:00+00:00",
  withdrawn_at: null,
  current_version: TESTER_AGREEMENT_VERSION,
  needs_reconsent: false,
};

describe("submitEnrollment", () => {
  it("posts the request to the enrollment route and returns the new enrollment", async () => {
    const { calls, fetcher } = recorder(JOINED_BODY);

    const outcome = await submitEnrollment(fetcher, FILLED);

    expect(outcome.ok && outcome.enrollment.enrolled).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe(ENROLLMENT_PATH);
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      role_cohort: "software_engineer",
      seniority: "senior",
      needs_sponsorship: false,
      accept_version: TESTER_AGREEMENT_VERSION,
    });
  });

  it("sends nothing for a form that is not complete", async () => {
    const { calls, fetcher } = recorder(JOINED_BODY);

    const outcome = await submitEnrollment(fetcher, { ...FILLED, agreed: false });

    expect(outcome.ok).toBe(false);
    expect(calls).toEqual([]);
  });

  it("says to reload when the agreement versions do not match (409), that nothing was recorded, and does not say which side is behind", async () => {
    const { fetcher } = recorder(new ApiError(409, "The tester agreement changed.", "CONFLICT"));

    const outcome = await submitEnrollment(fetcher, FILLED);

    expect(outcome).toEqual({ ok: false, message: AGREEMENT_CHANGED_MESSAGE });
    expect(AGREEMENT_CHANGED_MESSAGE).toBe(
      "The tester agreement on this page and the one on the server are not the same version. Nothing was recorded. Reload the page and read what it shows before you accept; if it keeps happening, try again in a few minutes.",
    );
    // Either side can be the one that is behind (the page, or a server not yet updated), so it does
    // not say "changed while this page was open", which is true of only one of them.
    expect(AGREEMENT_CHANGED_MESSAGE).not.toContain("while this page was open");
  });

  it("says when to try again after a rate limit", async () => {
    const { fetcher } = recorder(new ApiError(429, "x", "RATE_LIMITED", true, 600));

    const outcome = await submitEnrollment(fetcher, FILLED);

    expect(!outcome.ok && outcome.message).toBe("You are doing that too often. Try again in about 10 minutes.");
  });

  it("keeps the server's own message for any other refusal", async () => {
    const { fetcher } = recorder(new ApiError(422, "Invalid request.", "INVALID_INPUT"));
    expect(await submitEnrollment(fetcher, FILLED)).toEqual({ ok: false, message: "Invalid request." });
  });

  it("does not announce a success the server did not give: a 200 that is not 'enrolled'", async () => {
    const { fetcher } = recorder({ ...JOINED_BODY, enrolled: false });
    const outcome = await submitEnrollment(fetcher, FILLED);
    expect(outcome.ok).toBe(false);
  });

  it("does not guess from an answer it cannot read", async () => {
    const { fetcher } = recorder({ nope: true });
    const outcome = await submitEnrollment(fetcher, FILLED);
    expect(!outcome.ok && outcome.message).toContain("Reload the page");
  });
});

describe("withdrawEnrollment", () => {
  it("posts to the withdraw route and returns the new enrollment", async () => {
    const { calls, fetcher } = recorder({ ...JOINED_BODY, enrolled: false, withdrawn_at: "2026-10-07T09:00:00+00:00" });

    const outcome = await withdrawEnrollment(fetcher);

    expect(outcome.ok && outcome.enrollment.withdrawnAt).toBe("2026-10-07T09:00:00+00:00");
    expect(calls).toEqual([{ path: WITHDRAW_PATH, init: { method: "POST" } }]);
  });

  it("says why it failed, in the server's words or the shared rate-limit words", async () => {
    expect(await withdrawEnrollment(recorder(new ApiError(404, "You are not in the tester programme.", "NOT_FOUND")).fetcher)).toEqual({
      ok: false,
      message: "You are not in the tester programme.",
    });
    const limited = await withdrawEnrollment(recorder(new ApiError(429, "x", "RATE_LIMITED", true, 30)).fetcher);
    expect(!limited.ok && limited.message).toContain("too often");
  });

  it("falls back to its own sentence for something that is not an Error", async () => {
    const fetcher: EnrollmentFetcher = async () => {
      throw 5;
    };
    const outcome = await withdrawEnrollment(fetcher);
    expect(!outcome.ok && outcome.message).toBe("We could not record your withdrawal. Try again in a moment.");
  });
});

describe("enrollmentErrorMessage", () => {
  it("has a sentence for each code that matters here", () => {
    expect(enrollmentErrorMessage(new ApiError(409, "x", "CONFLICT"), "f")).toBe(AGREEMENT_CHANGED_MESSAGE);
    // the shared wording (rateLimitMessage.ts), so the two cannot drift apart
    expect(enrollmentErrorMessage(new ApiError(403, "x", "ENROLLMENT_REQUIRED"), "f")).toBe(ENROLLMENT_REQUIRED_MESSAGE);
    expect(enrollmentErrorMessage(new ApiError(429, "x", "RATE_LIMITED", true, 7200), "f")).toContain("about 2 hours");
    expect(enrollmentErrorMessage(new ApiError(500, "boom"), "f")).toBe("boom");
    expect(enrollmentErrorMessage("nope", "fallback")).toBe("fallback");
  });
});

// ── the page's state ───────────────────────────────────────────────────────

describe("enrollPageReducer", () => {
  const apply = (...events: Parameters<typeof enrollPageReducer>[1][]) =>
    events.reduce(enrollPageReducer, initialEnrollPageState);

  it("fills the form from an earlier enrollment once, and then leaves the person's edits alone", () => {
    const earlier = enrollment({ enrolled: false, roleCohort: "qa_sdet", seniority: "mid", withdrawnAt: "2026-10-07T00:00:00Z" });
    const seeded = apply({ type: "seed", enrollment: earlier });
    expect(seeded.form.roleCohort).toBe("qa_sdet");
    const edited = enrollPageReducer(seeded, { type: "role", value: "data_engineer" });
    expect(enrollPageReducer(edited, { type: "seed", enrollment: earlier }).form.roleCohort).toBe("data_engineer");
  });

  it("takes each field as it is chosen, and a value that is not on the list as nothing chosen", () => {
    const state = apply(
      { type: "role", value: "devops_sre" },
      { type: "seniority", value: "lead_plus" },
      { type: "sponsorship", value: "yes" },
      { type: "agreed", value: true },
    );
    expect(state.form).toEqual({ roleCohort: "devops_sre", seniority: "lead_plus", sponsorship: "yes", agreed: true });
    expect(enrollPageReducer(state, { type: "role", value: "astronaut" }).form.roleCohort).toBe("");
    expect(enrollPageReducer(state, { type: "seniority", value: "" }).form.seniority).toBe("");
  });

  it("shows the field errors only after a try to submit, and counts each try", () => {
    expect(initialEnrollPageState.showErrors).toBe(false);
    expect(initialEnrollPageState.attempts).toBe(0);
    expect(apply({ type: "submit_attempted" })).toMatchObject({ showErrors: true, attempts: 1 });
    expect(apply({ type: "submit_attempted" }, { type: "submit_attempted" }).attempts).toBe(2);
  });

  it("goes through a join: busy, then done with a notice and the box cleared, or failed with the message", () => {
    const filled = apply({ type: "agreed", value: true });
    const joining = enrollPageReducer(filled, { type: "join_started" });
    expect(joining.busy).toBe("joining");
    const failed = enrollPageReducer(joining, { type: "join_failed", message: "no" });
    expect(failed).toMatchObject({ busy: "idle", error: "no" });
    const joined = enrollPageReducer(joining, { type: "joined" });
    expect(joined).toMatchObject({ busy: "idle", error: null, notice: "joined" });
    expect(joined.form.agreed).toBe(false);
  });

  it("goes through a withdrawal: asked, cancelled or confirmed, done or failed", () => {
    const asked = apply({ type: "withdraw_asked" });
    expect(asked.confirmingWithdraw).toBe(true);
    expect(enrollPageReducer(asked, { type: "withdraw_cancelled" }).confirmingWithdraw).toBe(false);
    const started = enrollPageReducer(asked, { type: "withdraw_started" });
    expect(started.busy).toBe("withdrawing");
    expect(enrollPageReducer(started, { type: "withdraw_failed", message: "no" })).toMatchObject({
      busy: "idle",
      error: "no",
      confirmingWithdraw: true,
    });
    expect(enrollPageReducer(started, { type: "withdrew" })).toMatchObject({
      busy: "idle",
      confirmingWithdraw: false,
      notice: "withdrew",
    });
  });

  it("clears an old notice and error when a new action starts", () => {
    const joined = apply({ type: "joined" });
    expect(enrollPageReducer(joined, { type: "withdraw_asked" }).notice).toBeNull();
    expect(enrollPageReducer({ ...joined, notice: "withdrew" }, { type: "join_started" }).notice).toBeNull();
  });
});

describe("isEnrollmentRefusal", () => {
  it("is true for a 403 ENROLLMENT_REQUIRED and for nothing else", () => {
    expect(isEnrollmentRefusal(new ApiError(403, "x", "ENROLLMENT_REQUIRED"))).toBe(true);
    expect(isEnrollmentRefusal({ code: "ENROLLMENT_REQUIRED" })).toBe(true);
    for (const other of [
      new ApiError(403, "x", "FORBIDDEN"),
      new ApiError(403, "Forbidden"),
      new ApiError(409, "x", "CONFLICT"),
      new ApiError(429, "x", "RATE_LIMITED"),
      new Error("ENROLLMENT_REQUIRED"),
      "ENROLLMENT_REQUIRED",
      null,
      undefined,
    ]) {
      expect(isEnrollmentRefusal(other)).toBe(false);
    }
  });
});

// ── focus ──────────────────────────────────────────────────────────────────

describe("focusTargetAfterChange", () => {
  const idle = { confirmingWithdraw: false, notice: null } as const;

  it("moves focus to the confirmation when the person asks to withdraw, never to the destructive button", () => {
    expect(focusTargetAfterChange(idle, { confirmingWithdraw: true, notice: null })).toBe(ENROLL_FOCUS_IDS.confirm);
    // asking also clears an old notice (the reducer does), which must not send focus to it
    expect(focusTargetAfterChange({ confirmingWithdraw: false, notice: "joined" }, { confirmingWithdraw: true, notice: null })).toBe(
      ENROLL_FOCUS_IDS.confirm,
    );
  });

  it("returns focus to the Withdraw button when they cancel", () => {
    expect(focusTargetAfterChange({ confirmingWithdraw: true, notice: null }, idle)).toBe(ENROLL_FOCUS_IDS.withdraw);
  });

  it("moves focus to the result banner after a join or a withdrawal, which wins over the confirmation closing", () => {
    expect(focusTargetAfterChange(idle, { confirmingWithdraw: false, notice: "joined" })).toBe(ENROLL_FOCUS_IDS.notice);
    expect(focusTargetAfterChange({ confirmingWithdraw: true, notice: null }, { confirmingWithdraw: false, notice: "withdrew" })).toBe(
      ENROLL_FOCUS_IDS.notice,
    );
  });

  it("leaves focus alone when nothing it cares about changed, and when the same banner is still up", () => {
    expect(focusTargetAfterChange(idle, idle)).toBeNull();
    expect(focusTargetAfterChange({ confirmingWithdraw: true, notice: null }, { confirmingWithdraw: true, notice: null })).toBeNull();
    expect(focusTargetAfterChange({ confirmingWithdraw: false, notice: "joined" }, { confirmingWithdraw: false, notice: "joined" })).toBeNull();
  });

  it("uses ids that are distinct from the form's own", () => {
    const ids = [...Object.values(ENROLL_FOCUS_IDS), ...Object.values(ENROLL_FIELD_IDS)];
    expect(new Set(ids).size).toBe(ids.length);
    expect(ENROLL_FOCUS_IDS).toEqual({
      withdraw: "bj-enroll-withdraw",
      confirm: "bj-enroll-confirm",
      notice: "bj-enroll-notice",
    });
  });
});

// ── the gate ───────────────────────────────────────────────────────────────

const caps = (testerProgramRequired: boolean): CapabilitiesState => ({
  kind: "ready",
  capabilities: { telegram: false, telegramBotUsername: null, testerProgramRequired },
});
const READY = (e: Enrollment): EnrollmentLoad => ({ kind: "ready", enrollment: e });

describe("the exempt pages", () => {
  it("are the enrollment page, the two legal documents, Profile, Integrations and the new-password page, and only those", () => {
    expect([...GATE_EXEMPT_PATHS].sort()).toEqual([
      "/enroll",
      "/privacy",
      "/profile",
      "/profile/integrations",
      "/terms",
      "/update-password",
    ]);
    expect(INTEGRATIONS_PATH).toBe("/profile/integrations");
    expect(UPDATE_PASSWORD_PATH).toBe("/update-password");
  });

  it.each([
    "/enroll",
    "/privacy",
    "/terms",
    "/profile",
    "/profile/",
    "/profile/integrations",
    "/profile/integrations/",
    "/update-password",
    "/enroll?next=1",
    "/privacy#gmail",
  ])("%s is exempt", (path) => {
    expect(isGateExempt(path)).toBe(true);
  });

  it.each(["/", "/discover", "/applications", "/applications/123", "/practice", "/hiring-signals", "/Profile", "/profile/Integrations", "/profile/integrations/extra", "/ENROLL", "/privacy/extra", "/enrolled", "/update-password/extra"])(
    "%s is not (an unrecognised spelling is gated, the safe direction)",
    (path) => {
      expect(isGateExempt(path)).toBe(false);
    },
  );
});

describe("enrollmentGate", () => {
  const decide = (capabilities: CapabilitiesState, enrollmentState: EnrollmentLoad, pathname = "/discover"): GateDecision =>
    enrollmentGate({ capabilities, enrollment: enrollmentState, pathname });

  describe("when the programme is not required, nothing changes for anyone", () => {
    it.each([
      ["a person who has not joined", READY(NOT_ENROLLED)],
      ["a current tester", READY(enrollment())],
      ["a withdrawn tester", READY(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" }))],
      ["someone whose lookup is idle", { kind: "idle" } as EnrollmentLoad],
      ["someone whose lookup failed", { kind: "failed", message: "x" } as EnrollmentLoad],
      ["someone whose lookup is loading", { kind: "loading" } as EnrollmentLoad],
    ])("%s passes on every page", (_who, state) => {
      for (const pathname of ["/", "/discover", "/applications/9", "/practice", "/profile/integrations", "/enroll"]) {
        expect(decide(caps(false), state, pathname), pathname).toBe("pass");
      }
    });
  });

  it("waits, drawing nothing, while the server's settings are not known", () => {
    expect(decide({ kind: "checking" }, { kind: "idle" })).toBe("wait");
    expect(decide({ kind: "checking" }, READY(enrollment()))).toBe("wait");
  });

  it("gets out of the way when the server's settings could not be asked (the server still refuses, with a message that says to join)", () => {
    expect(decide({ kind: "unavailable" }, { kind: "idle" })).toBe("pass");
  });

  describe("when the programme is required", () => {
    it("waits for the lookup, without a flash of the page", () => {
      expect(decide(caps(true), { kind: "idle" })).toBe("wait");
      expect(decide(caps(true), { kind: "loading" })).toBe("wait");
    });

    it("says so, and offers a retry, when the lookup failed: never in or out silently", () => {
      expect(decide(caps(true), { kind: "failed", message: "down" })).toBe("retry");
    });

    it("sends a person who has not joined to the enrollment page, on every page that is not exempt", () => {
      for (const pathname of ["/", "/discover", "/hiring-signals", "/applications", "/applications/123", "/practice"]) {
        expect(decide(caps(true), READY(NOT_ENROLLED), pathname), pathname).toBe("enroll");
      }
    });

    // The pages that switch things off stay open to everyone: the server leaves them open (nothing
    // cheap is gated), and the background work they stop keeps running on a person's key whether
    // or not they are enrolled. Integrations is where a key is removed and Gmail is disconnected,
    // and where a saved search is paused or deleted.
    it.each([
      ["a person who never joined", READY(NOT_ENROLLED)],
      ["a person who withdrew", READY(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" }))],
      ["a person on an older agreement", READY(enrollment({ enrolled: false, needsReconsent: true }))],
    ])("lets %s reach Integrations, while Discover and Applications still send them to enroll", (_who, state) => {
      expect(decide(caps(true), state, INTEGRATIONS_PATH)).toBe("pass");
      expect(decide(caps(true), state, "/discover")).toBe("enroll");
      expect(decide(caps(true), state, "/applications")).toBe("enroll");
    });

    it("lets Integrations through while the lookup is still out or has failed too (it never waits on the person's enrollment)", () => {
      for (const state of [{ kind: "idle" }, { kind: "loading" }, { kind: "failed", message: "x" }] as EnrollmentLoad[]) {
        expect(decide(caps(true), state, INTEGRATIONS_PATH), state.kind).toBe("pass");
      }
    });

    // A person who followed an emailed reset link is sent to the new-password page from every
    // address. Consent to a research programme must not be a condition of recovering an account,
    // and Profile (account deletion) is unreachable from there, so the page must not be gated.
    it("never stands between a person in password recovery and the new-password form, from any address they land on", () => {
      for (const landedOn of ["/", "/profile", "/profile/integrations", "/applications/1", "/discover", "/enroll", "/privacy"]) {
        const target = recoveryRedirect(true, landedOn);
        const page = target === null ? landedOn : target;
        expect(page, landedOn).toBe(UPDATE_PASSWORD_PATH);
        for (const state of [READY(NOT_ENROLLED), READY(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" })), { kind: "idle" } as EnrollmentLoad]) {
          expect(decide(caps(true), state, page), `${landedOn} ${state.kind}`).toBe("pass");
        }
      }
    });

    it("sends a withdrawn tester and one on an older agreement there too", () => {
      expect(decide(caps(true), READY(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" })))).toBe("enroll");
      expect(decide(caps(true), READY(enrollment({ enrolled: false, needsReconsent: true })))).toBe("enroll");
    });

    it("lets a current tester through everywhere", () => {
      for (const pathname of ["/", "/discover", "/applications/123", "/profile"]) {
        expect(decide(caps(true), READY(enrollment()), pathname)).toBe("pass");
      }
    });

    it("never stands in front of the exempt pages, whatever the lookup says", () => {
      const states: EnrollmentLoad[] = [
        { kind: "idle" },
        { kind: "loading" },
        { kind: "failed", message: "x" },
        READY(NOT_ENROLLED),
        READY(enrollment()),
      ];
      for (const state of states) {
        for (const pathname of ["/enroll", "/privacy", "/terms", "/profile", "/profile/integrations", "/update-password"]) {
          expect(decide(caps(true), state, pathname), `${state.kind} ${pathname}`).toBe("pass");
        }
      }
    });

    it("does not wait for the server's settings on an exempt page either", () => {
      expect(decide({ kind: "checking" }, { kind: "idle" }, "/profile")).toBe("pass");
    });
  });
});

describe("shouldLoadEnrollment", () => {
  it("asks only when the programme is required and nothing has been asked yet", () => {
    expect(shouldLoadEnrollment(caps(true), { kind: "idle" })).toBe(true);
    expect(shouldLoadEnrollment(caps(true), { kind: "loading" })).toBe(false);
    expect(shouldLoadEnrollment(caps(true), { kind: "failed", message: "x" })).toBe(false);
    expect(shouldLoadEnrollment(caps(true), READY(enrollment()))).toBe(false);
    expect(shouldLoadEnrollment(caps(false), { kind: "idle" })).toBe(false);
    expect(shouldLoadEnrollment({ kind: "checking" }, { kind: "idle" })).toBe(false);
    expect(shouldLoadEnrollment({ kind: "unavailable" }, { kind: "idle" })).toBe(false);
  });
});

describe("enrollmentTitleFor", () => {
  it("is the enrollment page's title whenever the gate draws that page (or its retry), at ANY address", () => {
    for (const decision of ["enroll", "retry"] as GateDecision[]) {
      for (const pathname of ["/", "/discover", "/applications/123", "/practice", "/enroll"]) {
        expect(enrollmentTitleFor(decision, pathname), `${decision} ${pathname}`).toBe("Tester programme -- Between Jobs");
      }
    }
    expect(ENROLL_PAGE_NAME).toBe("Tester programme");
  });

  it("is the address's own title when the gate lets the page through or is waiting, so it is put back once the person joins", () => {
    for (const decision of ["pass", "wait"] as GateDecision[]) {
      expect(enrollmentTitleFor(decision, "/discover")).toBe("Between Jobs");
      expect(enrollmentTitleFor(decision, "/")).toBe("Between Jobs");
      expect(enrollmentTitleFor(decision, "/privacy")).toBe("Privacy Policy -- Between Jobs");
      expect(enrollmentTitleFor(decision, "/terms/")).toBe("Terms of Service -- Between Jobs");
      expect(enrollmentTitleFor(decision, "/enroll")).toBe("Tester programme -- Between Jobs");
    }
  });

  it("agrees with the shell's own rule for every address, whenever the gate lets the page through", () => {
    for (const pathname of ["/", "/discover", "/privacy", "/terms", "/enroll", "/profile", "/profile/integrations"]) {
      expect(enrollmentTitleFor("pass", pathname)).toBe(documentTitleFor("app", pathname));
    }
  });
});

// ── the Profile line ───────────────────────────────────────────────────────

describe("profileLineStatus", () => {
  it("is hidden on a server with no programme, for someone who never joined", () => {
    expect(profileLineStatus(caps(false), READY(NOT_ENROLLED))).toBeNull();
    expect(profileLineStatus(caps(false), { kind: "idle" })).toBeNull();
    expect(profileLineStatus(caps(false), { kind: "loading" })).toBeNull();
    expect(profileLineStatus(caps(false), { kind: "failed", message: "x" })).toBeNull();
    expect(profileLineStatus({ kind: "checking" }, { kind: "idle" })).toBeNull();
  });

  it("is hidden for someone who withdrew, when the programme is not required", () => {
    expect(profileLineStatus(caps(false), READY(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T00:00:00Z" })))).toBeNull();
  });

  it("is shown to someone already in it, required or not", () => {
    expect(profileLineStatus(caps(false), READY(enrollment()))).toBe("You are in the tester programme.");
    expect(profileLineStatus(caps(true), READY(enrollment()))).toBe("You are in the tester programme.");
    expect(profileLineStatus({ kind: "unavailable" }, READY(enrollment()))).toBe("You are in the tester programme.");
  });

  it("is shown to someone on an older agreement, required or not", () => {
    const older = READY(enrollment({ enrolled: false, needsReconsent: true }));
    expect(profileLineStatus(caps(false), older)).toContain("agreement has changed");
    expect(profileLineStatus(caps(true), older)).toContain("agreement has changed");
  });

  it("is shown when the programme is required, even before the person's own state is known", () => {
    expect(profileLineStatus(caps(true), { kind: "idle" })).toBe("Joining the tester programme is required on this server.");
    expect(profileLineStatus(caps(true), { kind: "failed", message: "x" })).toBe("Joining the tester programme is required on this server.");
    expect(profileLineStatus(caps(true), READY(NOT_ENROLLED))).toBe("You have not joined the tester programme.");
  });

  it("says when a tester withdrew, if the programme is required", () => {
    expect(
      profileLineStatus(caps(true), READY(enrollment({ enrolled: false, withdrawnAt: "2026-10-07T09:00:00+00:00" }))),
    ).toBe("You withdrew from the tester programme on October 7, 2026.");
    expect(profileLineStatus(caps(true), READY(enrollment({ enrolled: false, withdrawnAt: "later" })))).toBe(
      "You withdrew from the tester programme.",
    );
  });
});
