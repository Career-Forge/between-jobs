import type { FormEvent, ReactNode, Ref } from "react";
import { Link } from "react-router-dom";
import { TESTER_AGREEMENT, TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import {
  ENROLL_FIELD_IDS,
  ENROLL_FOCUS_IDS,
  ROLE_COHORTS,
  SENIORITIES,
  SPONSORSHIP_OPTIONS,
  SPONSORSHIP_QUESTION,
  SPONSORSHIP_REASON,
  agreementSkew,
  agreementVersionsDiffer,
  enrollmentStatus,
  formatEnrollmentDate,
  roleCohortLabel,
  seniorityLabel,
  sponsorshipFromApi,
  validateEnrollForm,
  type Enrollment,
  type EnrollPageState,
  type SponsorshipAnswer,
} from "../lib/enrollment";
import { BlockView } from "./LegalDocumentView";

// The tester-programme page: the agreement, the form that joins, and for
// someone who has joined, what they gave and the way to withdraw. A pure function of its props,
// with no hooks, so a test can render it directly; the page state lives in pages/Enroll.tsx
// (the reducer is lib/enrollment.ts), which also hands in errorRef so it can move focus to the
// error when one appears.
//
// The agreement is drawn from content/testerAgreement.ts with the same blocks the Privacy Policy
// uses, in a box that scrolls and can be focused (so a keyboard user can scroll it) under its own
// heading. EVERY link to the Terms and the Privacy Policy on the page, the ones inside the
// agreement as well as the two in the checkbox sentence, opens in a new tab, so reading them never
// costs the person what they have already filled in (the form lives only in this page's state).
//
// Controls that go away when they are used (the Withdraw button, the join button) would drop
// keyboard focus to the document, so the page (pages/Enroll.tsx) moves it to the thing that
// replaces them. This view only gives those targets their ids and tabIndex -1
// (lib/enrollment.ts: ENROLL_FOCUS_IDS).

export interface EnrollmentActions {
  setRole: (value: string) => void;
  setSeniority: (value: string) => void;
  setSponsorship: (value: SponsorshipAnswer) => void;
  setAgreed: (value: boolean) => void;
  submit: () => void;
  askWithdraw: () => void;
  cancelWithdraw: () => void;
  confirmWithdraw: () => void;
}

const AGREEMENT_HEADING_ID = "bj-agreement-heading";
const ERROR_IDS = {
  role: "bj-enroll-role-error",
  seniority: "bj-enroll-seniority-error",
  agreed: "bj-enroll-agreed-error",
} as const;

function NewTabLink({ href, children }: { href: string; children: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <span className="bj-visually-hidden"> (opens in a new tab)</span>
    </a>
  );
}

function AgreementText() {
  return (
    <section aria-labelledby={AGREEMENT_HEADING_ID} className="bj-card bj-enroll-agreement-card">
      <h2 id={AGREEMENT_HEADING_ID}>{TESTER_AGREEMENT.title}</h2>
      <p className="bj-muted bj-small">
        Version {TESTER_AGREEMENT_VERSION}. It is short, and it scrolls: read all of it before you
        join.
      </p>
      <div
        className="bj-legal bj-agreement"
        role="region"
        aria-label={`${TESTER_AGREEMENT.title}, scrollable`}
        tabIndex={0}
      >
        {TESTER_AGREEMENT.sections.map((section) => (
          <section key={section.id} id={`bj-agreement-${section.id}`}>
            <h3>{section.heading}</h3>
            {section.blocks.map((block, index) => (
              <BlockView key={index} block={block} newTabLinks />
            ))}
          </section>
        ))}
      </div>
    </section>
  );
}

function Banner({
  children,
  role,
  focusId,
}: {
  children: ReactNode;
  role?: "status" | "alert";
  // Gives the banner an id and lets it take focus (not through the tab order), for the one that
  // reports the result of what the person just did.
  focusId?: string;
}) {
  return (
    <div
      className="bj-card bj-enroll-banner"
      role={role}
      id={focusId}
      tabIndex={focusId === undefined ? undefined : -1}
    >
      {children}
    </div>
  );
}

function StatusBanners({
  enrollment,
  page,
  programmeRequired,
}: {
  enrollment: Enrollment;
  page: EnrollPageState;
  programmeRequired: boolean;
}) {
  const status = enrollmentStatus(enrollment);
  const skew = agreementSkew(enrollment);
  const withdrewOn = formatEnrollmentDate(enrollment.withdrawnAt);
  return (
    <>
      {page.notice === "joined" && (
        <Banner role="status" focusId={ENROLL_FOCUS_IDS.notice}>
          <strong>You are in the tester programme. Thank you.</strong>
          <span>
            Your answers are recorded below. <Link to="/">Go to Today</Link>
          </span>
        </Banner>
      )}
      {page.notice === "withdrew" && (
        <Banner role="status" focusId={ENROLL_FOCUS_IDS.notice}>
          <strong>You have withdrawn from the tester programme.</strong>
          <span>Your account and your data are unchanged. You can join again below.</span>
        </Banner>
      )}
      {programmeRequired && status !== "joined" && (
        <Banner>
          <strong>Joining the tester programme is required on this server.</strong>
          <span>
            It comes before the features that call an AI, a search or a document service, and the
            rest of the app opens when you join. These pages stay open: your Profile page, where
            you can delete your account, and your Integrations page, where you can remove a key,
            disconnect Gmail, and pause or delete a saved search.
          </span>
        </Banner>
      )}
      {status === "needs_reconsent" && (
        <Banner>
          <strong>The tester agreement has changed since you accepted it.</strong>
          <span>
            You accepted version {enrollment.consentVersion ?? "unknown"}; the current version is{" "}
            {enrollment.currentVersion ?? TESTER_AGREEMENT_VERSION}. Read it again and accept it
            to carry on.
          </span>
        </Banner>
      )}
      {status === "withdrawn" && page.notice !== "withdrew" && (
        <Banner>
          <strong>
            {withdrewOn === null
              ? "You withdrew from the tester programme."
              : `You withdrew from the tester programme on ${withdrewOn}.`}
          </strong>
          <span>Your account and your data are unchanged. You can join again below.</span>
        </Banner>
      )}
      {skew === "page_older" && (
        <div className="bj-card bj-enroll-banner" role="alert">
          <strong>This page shows an older version of the agreement than the server has.</strong>
          <span>Reload the page, read the new version and accept it. Nothing is recorded until you do.</span>
        </div>
      )}
      {skew === "page_newer" && (
        <div className="bj-card bj-enroll-banner" role="status">
          <strong>The server has not caught up with this version of the agreement yet.</strong>
          <span>Try again in a few minutes. Nothing is recorded until it has.</span>
        </div>
      )}
      {skew === "unknown" && (
        <div className="bj-card bj-enroll-banner" role="alert">
          <strong>
            This page and the server do not show the same version of the agreement (this page:{" "}
            {TESTER_AGREEMENT_VERSION}; the server: {enrollment.currentVersion ?? "unknown"}).
          </strong>
          <span>
            Reload the page. If they still differ, try again in a few minutes. Nothing is recorded
            until they match.
          </span>
        </div>
      )}
    </>
  );
}

function sponsorshipText(enrollment: Enrollment): string {
  const answer = sponsorshipFromApi(enrollment.needsSponsorship);
  return SPONSORSHIP_OPTIONS.find((option) => option.value === answer)?.label ?? "Prefer not to say";
}

function Joined({
  enrollment,
  page,
  programmeRequired,
  actions,
}: {
  enrollment: Enrollment;
  page: EnrollPageState;
  programmeRequired: boolean;
  actions: EnrollmentActions;
}) {
  const busy = page.busy !== "idle";
  const accepted = formatEnrollmentDate(enrollment.consentedAt);
  return (
    <section className="bj-card" aria-labelledby="bj-enroll-joined-heading">
      <h2 id="bj-enroll-joined-heading">What you gave us</h2>
      <dl className="bj-enroll-facts">
        <dt>Role</dt>
        <dd>{roleCohortLabel(enrollment.roleCohort) ?? "Not recorded"}</dd>
        <dt>Seniority</dt>
        <dd>{seniorityLabel(enrollment.seniority) ?? "Not recorded"}</dd>
        <dt>Sponsorship answer</dt>
        <dd>{sponsorshipText(enrollment)}</dd>
        <dt>Agreement you accepted</dt>
        <dd>
          Version {enrollment.consentVersion ?? "unknown"}
          {accepted === null ? "" : `, on ${accepted}`}
        </dd>
      </dl>
      {!page.confirmingWithdraw ? (
        <div className="bj-actions">
          <button
            type="button"
            id={ENROLL_FOCUS_IDS.withdraw}
            onClick={() => actions.askWithdraw()}
            disabled={busy}
          >
            Withdraw from the programme
          </button>
        </div>
      ) : (
        <div
          className="bj-enroll-confirm"
          role="group"
          aria-label="Confirm withdrawing"
          aria-describedby="bj-enroll-confirm-text"
          id={ENROLL_FOCUS_IDS.confirm}
          tabIndex={-1}
        >
          <p>
            <strong>Withdraw from the tester programme?</strong>
          </p>
          <p id="bj-enroll-confirm-text">
            This marks you as withdrawn and leaves your usage out of the programme&rsquo;s counts,
            apart from a count of how many people withdrew, by role. It does not delete your
            account or your data.
            {programmeRequired
              ? " This server asks testers to stay enrolled to start the costly features, so withdrawing also stops you starting them until you join again. Saved searches and Gmail reply checking keep running until you pause, delete or disconnect them."
              : ""}
          </p>
          <div className="bj-actions">
            <button
              type="button"
              className="bj-danger"
              onClick={() => actions.confirmWithdraw()}
              disabled={busy}
            >
              {page.busy === "withdrawing" ? "Withdrawing..." : "Yes, withdraw"}
            </button>
            <button type="button" onClick={() => actions.cancelWithdraw()} disabled={busy}>
              Keep me in the programme
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function JoinForm({
  enrollment,
  page,
  actions,
}: {
  enrollment: Enrollment;
  page: EnrollPageState;
  actions: EnrollmentActions;
}) {
  const status = enrollmentStatus(enrollment);
  // Whichever side is behind, what would be recorded is not what is on screen, and the server
  // would refuse it.
  const stale = agreementVersionsDiffer(enrollment);
  const busy = page.busy !== "idle";
  const errors = page.showErrors ? validateEnrollForm(page.form) : {};
  const label =
    status === "withdrawn"
      ? "Join again"
      : status === "needs_reconsent"
        ? "Accept the new agreement"
        : "Join the programme";

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    actions.submit();
  }

  return (
    <form className="bj-card bj-enroll-form" onSubmit={onSubmit} noValidate>
      <h2>{label}</h2>

      <label className="bj-field" htmlFor={ENROLL_FIELD_IDS.roleCohort}>
        <span>The role you are looking for</span>
        <select
          id={ENROLL_FIELD_IDS.roleCohort}
          value={page.form.roleCohort}
          disabled={busy}
          aria-required="true"
          aria-invalid={errors.roleCohort !== undefined}
          aria-describedby={errors.roleCohort ? ERROR_IDS.role : undefined}
          onChange={(e) => actions.setRole(e.target.value)}
        >
          <option value="">Choose a role</option>
          {ROLE_COHORTS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {errors.roleCohort && (
          <span id={ERROR_IDS.role} className="bj-error">
            {errors.roleCohort}
          </span>
        )}
      </label>

      <label className="bj-field" htmlFor={ENROLL_FIELD_IDS.seniority}>
        <span>Your seniority</span>
        <select
          id={ENROLL_FIELD_IDS.seniority}
          value={page.form.seniority}
          disabled={busy}
          aria-required="true"
          aria-invalid={errors.seniority !== undefined}
          aria-describedby={errors.seniority ? ERROR_IDS.seniority : undefined}
          onChange={(e) => actions.setSeniority(e.target.value)}
        >
          <option value="">Choose your seniority</option>
          {SENIORITIES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {errors.seniority && (
          <span id={ERROR_IDS.seniority} className="bj-error">
            {errors.seniority}
          </span>
        )}
      </label>

      <fieldset className="bj-enroll-fieldset" aria-describedby="bj-enroll-sponsorship-reason">
        <legend>
          {SPONSORSHIP_QUESTION} <span className="bj-muted">(optional)</span>
        </legend>
        <div className="bj-enroll-radios">
          {SPONSORSHIP_OPTIONS.map((option) => (
            <label key={option.value} className="bj-enroll-choice">
              <input
                type="radio"
                name="bj-enroll-sponsorship"
                value={option.value}
                checked={page.form.sponsorship === option.value}
                disabled={busy}
                onChange={() => actions.setSponsorship(option.value)}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
        <p id="bj-enroll-sponsorship-reason" className="bj-muted bj-small">
          {SPONSORSHIP_REASON}
        </p>
      </fieldset>

      <div className="bj-enroll-agree">
        <label className="bj-enroll-choice" htmlFor={ENROLL_FIELD_IDS.agreed}>
          <input
            id={ENROLL_FIELD_IDS.agreed}
            type="checkbox"
            checked={page.form.agreed}
            disabled={busy}
            aria-required="true"
            aria-invalid={errors.agreed !== undefined}
            aria-describedby={errors.agreed ? ERROR_IDS.agreed : undefined}
            onChange={(e) => actions.setAgreed(e.target.checked)}
          />
          <span>
            I have read and agree to the <a href={`#${AGREEMENT_HEADING_ID}`}>Tester Agreement</a>,
            the <NewTabLink href="/terms">Terms</NewTabLink> and the{" "}
            <NewTabLink href="/privacy">Privacy Policy</NewTabLink>
          </span>
        </label>
        {errors.agreed && (
          <span id={ERROR_IDS.agreed} className="bj-error">
            {errors.agreed}
          </span>
        )}
      </div>

      <div className="bj-actions">
        <button type="submit" className="bj-primary" disabled={busy || stale}>
          {page.busy === "joining" ? "Joining..." : label}
        </button>
      </div>
    </form>
  );
}

export function EnrollmentView({
  enrollment,
  page,
  programmeRequired,
  errorRef,
  actions,
}: {
  enrollment: Enrollment;
  page: EnrollPageState;
  // The server requires the programme (GET /capabilities), which changes what is said around the
  // form and what withdrawing does.
  programmeRequired: boolean;
  errorRef?: Ref<HTMLDivElement>;
  actions: EnrollmentActions;
}) {
  const status = enrollmentStatus(enrollment);
  return (
    <article className="bj-enroll">
      <h1>Tester programme</h1>
      <StatusBanners enrollment={enrollment} page={page} programmeRequired={programmeRequired} />
      {page.error && (
        <div className="bj-card bj-enroll-banner bj-error" role="alert" ref={errorRef} tabIndex={-1}>
          {page.error}
        </div>
      )}
      {status === "joined" && (
        <Joined enrollment={enrollment} page={page} programmeRequired={programmeRequired} actions={actions} />
      )}
      <AgreementText />
      {status !== "joined" && <JoinForm enrollment={enrollment} page={page} actions={actions} />}
      {status === "needs_reconsent" && (
        <Joined enrollment={enrollment} page={page} programmeRequired={programmeRequired} actions={actions} />
      )}
    </article>
  );
}

// The page's own loading line: nothing about the person's state is known yet, so nothing is
// claimed about it.
export function EnrollmentLoadingView() {
  return (
    <article className="bj-enroll">
      <h1>Tester programme</h1>
      <p className="bj-muted" role="status">
        Checking where you stand.
      </p>
    </article>
  );
}

// The lookup failed (the gate's retry state, and the page's own): said plainly, never read as
// "in" or "out", with a way to try again.
export function EnrollmentRetryView({
  message,
  programmeRequired,
  onRetry,
}: {
  message: string;
  // The server requires the programme (said in the second line only when it is true: a server
  // that does not require it has no such rule to name).
  programmeRequired: boolean;
  onRetry: () => void;
}) {
  return (
    <article className="bj-enroll">
      <div className="bj-card" role="alert">
        <h2>We could not check your tester enrollment</h2>
        <p>{message}</p>
        <p className="bj-muted bj-small">
          Nothing was changed.
          {programmeRequired
            ? " This server asks testers to join the programme before they use the costly features."
            : ""}
        </p>
        <div className="bj-actions">
          <button type="button" className="bj-primary" onClick={() => onRetry()}>
            Try again
          </button>
        </div>
      </div>
    </article>
  );
}
