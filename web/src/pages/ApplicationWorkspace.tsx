import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useParams } from "react-router-dom";
import { DocumentLayout } from "../components/paper/DocumentLayout";
import { JumpBar } from "../components/paper/JumpBar";
import { NEXT_STEP_FOCUS_ID, NextStepBand } from "../components/paper/NextStepBand";
import { PageHeader } from "../components/paper/PageHeader";
import { FailureState, LoadingState } from "../components/paper/StateViews";
import { PanelSections } from "../components/workspace/PanelSections";
import { ResumeSections, type Missing } from "../components/workspace/ResumeSections";
import { ReviewSection } from "../components/workspace/ReviewSection";
import { WorkspaceHeader, webAddress } from "../components/workspace/WorkspaceHeader";
import { ApiError, apiFetch } from "../lib/api";
import { gmailAccess } from "../lib/gmailAccess";
import { MODEL_CHOICE_LABEL, modelKeyOf, type ModelKeyReading } from "../lib/modelKey";
import { nextStepWithRow, type NextStepInput, type NextStepOutcome, type SetupCheck } from "../lib/nextStep";
import { WORKSPACE_INTENTS, workspaceNotFound, type WorkspaceFacts } from "../lib/nextStep/pages/workspace";
import type { CanonicalProfile } from "../lib/profileTypes";
import { friendlyApiMessage } from "../lib/rateLimitMessage";
import { MODEL_PROVIDER_PATH } from "../lib/settingsPaths";
import { clockTime } from "../lib/todayFeed";
import { formatTrailDate } from "../lib/trail";
import { useCapabilities } from "../lib/useCapabilities";
import { useGeneration } from "../lib/useGeneration";
import { useHiringSignalsStatus } from "../lib/useHiringSignalsStatus";
import { useScrollToHash } from "../lib/useScrollToHash";
import { SECTION_IDS, HONEST_READ_HEADING_ID, workspaceJumpGroups } from "../lib/workspaceSections";
import { generationFact, gmailFactOf } from "../lib/workspaceFacts";
import { stateSummary } from "../lib/workspaceTrails";
import type { WorkspaceApplication } from "../lib/workspaceTypes";
import type { ApplicationStatus } from "../lib/applicationsTypes";
import { latestAttempt } from "../lib/scoreBreakdown";
import "../styles/workspace.css";

// The application workspace: the page a person works in for one role. Its parts, in the order of the
// jump bar and of the page:
//
//   Resume     Tailor (#ch-sources) · Draft (#ch-draft) · Generate (#generate) · Review (#ch-check)
//   Research   Company intel · Contacts · Events · Hiring posts (only while the server has it on)
//   Prepare    Practice · Your play
//
// components/workspace/ResumeSections.tsx and ReviewSection.tsx are the resume part,
// components/workspace/PanelSections.tsx is where the research and prepare panels sit (and the
// convention they follow), and lib/workspaceSections.ts is the one list of ids. This file is the
// reading: the requests, the next-step band's facts, and which frame the page is in.
//
// Requests. GET /applications/{id} and GET /profile/current are the page's own data (a 404 on the
// profile is tolerated: the page still renders and each part that needs a profile says so, with a link);
// GET /credentials is read only to know whether a usable model key is saved, so that a part that needs
// one says so instead of firing a request that is known to fail. The resume part's own requests are the
// generation hook's (lib/useGeneration.ts) and its panels'. Changing the status is
// POST /applications/{id}/stage followed by a re-read of the application.
//
// The frames. Loading: the breadcrumb, a placeholder, and the band's eyebrow only. Not found (a 404 on
// the application): the band says so and links back to Applications; asking again cannot help. Failed:
// the page's own data could not be read; the band says what failed and offers Try again, and the body
// holds a callout with no Retry of its own. Ready: everything above.

type Read<T> = { kind: "loading" } | { kind: "ready"; value: T } | { kind: "failed"; at: string; message: string } | { kind: "not_found" };

interface ProfileVersion {
  id: string;
  canonical_json: CanonicalProfile;
}

const LOADING: Read<never> = { kind: "loading" };

export default function ApplicationWorkspace() {
  const { id } = useParams<{ id: string }>();
  // Another application is another page: nothing carries over from the last one.
  return id === undefined ? null : <Workspace key={id} id={id} />;
}

function Workspace({ id }: { id: string }) {
  const [application, setApplication] = useState<Read<WorkspaceApplication>>(LOADING);
  // `null` is "no active profile" (the 404): not a failure.
  const [profile, setProfile] = useState<Read<CanonicalProfile | null>>(LOADING);
  const [credentials, setCredentials] = useState<Read<readonly Record<string, unknown>[]>>(LOADING);
  const [statusBusy, setStatusBusy] = useState(false);
  const [statusProblem, setStatusProblem] = useState<{ source: "select" | "submit"; message: string; retry: () => void } | null>(null);

  const loadApplication = useCallback(async (quiet = false) => {
    if (!quiet) setApplication(LOADING);
    try {
      const row = await apiFetch<WorkspaceApplication>(`/applications/${encodeURIComponent(id)}`);
      setApplication({ kind: "ready", value: row });
      return true;
    } catch (e) {
      if (quiet) return false;
      if (e instanceof ApiError && (e.status === 404 || e.code === "NOT_FOUND")) setApplication({ kind: "not_found" });
      else setApplication({ kind: "failed", at: clockTime(new Date()), message: e instanceof Error ? e.message : "Failed to load" });
      return false;
    }
  }, [id]);

  const loadSetup = useCallback(async () => {
    setProfile(LOADING);
    setCredentials(LOADING);
    // A missing profile does not block the workspace. Company intel and the other research panels do not
    // read it, and generation says what is missing and links to the fix. The panels that edit the
    // resume's own document (header, structure, settings, and Tailor) cannot work without one -- the
    // server answers them with SETUP_REQUIRED -- so with no profile they give way to a note.
    const profileRead = apiFetch<ProfileVersion>("/profile/current").then(
      (version): Read<CanonicalProfile | null> => ({ kind: "ready", value: version.canonical_json }),
      (e): Read<CanonicalProfile | null> =>
        e instanceof ApiError && e.status === 404
          ? { kind: "ready", value: null }
          : { kind: "failed", at: clockTime(new Date()), message: e instanceof Error ? e.message : "Failed to load" },
    );
    const credentialsRead = apiFetch<Record<string, unknown>[]>("/credentials").then(
      (rows): Read<readonly Record<string, unknown>[]> => ({ kind: "ready", value: rows }),
      (e): Read<readonly Record<string, unknown>[]> => ({ kind: "failed", at: clockTime(new Date()), message: e instanceof Error ? e.message : "Failed to load" }),
    );
    // Each answer is drawn as it arrives; neither waits for the other.
    void profileRead.then(setProfile);
    void credentialsRead.then(setCredentials);
  }, []);

  useEffect(() => {
    void loadApplication();
  }, [loadApplication]);
  useEffect(() => {
    void loadSetup();
  }, [loadSetup]);

  // One stage change, from the select or from "I submitted this". It is the same request the
  // Applications page makes; the page re-reads the application afterwards, so the band follows.
  const changeStatus = useCallback(
    async (newStatus: ApplicationStatus, source: "select" | "submit") => {
      setStatusBusy(true);
      setStatusProblem(null);
      try {
        await apiFetch(`/applications/${encodeURIComponent(id)}/stage`, {
          method: "POST",
          body: JSON.stringify({ new_status: newStatus, idempotency_key: crypto.randomUUID() }),
        });
      } catch (e) {
        setStatusProblem({ source, message: friendlyApiMessage(e, "Failed to change status"), retry: () => void changeStatus(newStatus, source) });
        setStatusBusy(false);
        return;
      }
      const read = await loadApplication(true);
      if (!read) {
        setStatusProblem({
          source,
          message: "The status was changed, but this page couldn't read it back. Reload the page to see it.",
          retry: () => void loadApplication(true).then((ok) => ok && setStatusProblem(null)),
        });
      }
      setStatusBusy(false);
    },
    [id, loadApplication],
  );

  const profileCheck: SetupCheck<{ active: boolean; importWaiting: boolean }> =
    profile.kind === "ready"
      ? { state: "ready", value: { active: profile.value !== null, importWaiting: false } }
      : profile.kind === "failed"
        ? { state: "failed", call: "GET /profile/current", at: profile.at }
        : { state: "loading" };
  const credentialsCheck: SetupCheck<{ modelKey: ModelKeyReading }> =
    credentials.kind === "ready"
      ? { state: "ready", value: { modelKey: modelKeyOf(credentials.value) } }
      : credentials.kind === "failed"
        ? { state: "failed", call: "GET /credentials", at: credentials.at }
        : { state: "loading" };

  const retrySetup = () => void loadSetup();

  if (application.kind === "ready" && profile.kind === "ready") {
    return (
      <ReadyWorkspace
        id={id}
        application={application.value}
        profile={profile.value}
        credentials={credentials}
        profileCheck={profileCheck}
        credentialsCheck={credentialsCheck}
        statusBusy={statusBusy}
        statusProblem={statusProblem}
        onStatus={(status, source) => void changeStatus(status, source)}
        onReload={() => void loadApplication(true)}
        onRetrySetup={retrySetup}
      />
    );
  }

  // The frames that have no application to show. The band is decided by the same rows as the rest of
  // the app, except where there is nothing for them to be about.
  let body: ReactNode = <LoadingState rows={4} rowHeight={56} label="Loading..." />;
  let outcome: NextStepOutcome = { status: "loading" };
  let crumb = "Loading...";

  if (application.kind === "not_found") {
    crumb = "Not found";
    body = null;
    outcome = { status: "ready", step: workspaceNotFound(id) };
  } else if (application.kind === "failed" || profile.kind === "failed") {
    crumb = "Application";
    const input: NextStepInput = {
      page: "workspace",
      signedIn: true,
      profile: profileCheck,
      credentials: credentialsCheck,
      pageFailure:
        application.kind === "failed"
          ? { what: "Couldn't load this application", call: `GET /applications/${id}`, at: application.at, intent: "retry" }
          : null,
    };
    outcome = nextStepWithRow(input).outcome;
    const message = application.kind === "failed" ? application.message : profile.kind === "failed" ? profile.message : "";
    body = <FailureState message={`Couldn't read ${application.kind === "failed" ? "this application" : "your profile"}: ${message}`} />;
  }

  return (
    <>
      <PageHeader title="Application" breadcrumb={[{ label: "Applications", to: "/applications" }, { label: crumb }]} />
      <NextStepBand
        outcome={outcome}
        onAction={(intent) => {
          if (intent === "retry") void loadApplication();
          if (intent === "retry-setup") retrySetup();
        }}
      />
      {body}
    </>
  );
}

interface ReadyProps {
  id: string;
  application: WorkspaceApplication;
  profile: CanonicalProfile | null;
  credentials: Read<readonly Record<string, unknown>[]>;
  profileCheck: SetupCheck<{ active: boolean; importWaiting: boolean }>;
  credentialsCheck: SetupCheck<{ modelKey: ModelKeyReading }>;
  statusBusy: boolean;
  statusProblem: { source: "select" | "submit"; message: string; retry: () => void } | null;
  onStatus: (status: ApplicationStatus, source: "select" | "submit") => void;
  onReload: () => void;
  onRetrySetup: () => void;
}

// What Tailor and Generate cannot run without, when the page already knows a usable model key is not
// saved. Only a definite answer counts: a credentials read that failed or has not come back claims
// nothing, and the server's own refusal still shows where it is asked.
function modelSetupOf(reading: ModelKeyReading | undefined): Missing | null {
  if (reading === undefined || reading === "ready") return null;
  return {
    message: "Drafting and tailoring run on your own model, and no usable model key is saved.",
    missing: [{ label: reading === "needs_model" ? MODEL_CHOICE_LABEL : "Add a model key", to: MODEL_PROVIDER_PATH }],
  };
}

function ReadyWorkspace({ id, application, profile, credentials, profileCheck, credentialsCheck, statusBusy, statusProblem, onStatus, onReload, onRetrySetup }: ReadyProps) {
  const location = useLocation();
  const controller = useGeneration(id, application.resume_exists);
  const hiring = useHiringSignalsStatus();
  const gmail = gmailAccess(useCapabilities());
  const [now] = useState(() => new Date());
  const [refocus, setRefocus] = useState(false);

  const snapshot = application.snapshot;
  const postingUrl = webAddress(snapshot?.source_url);
  const modelKey = credentialsCheck.state === "ready" ? credentialsCheck.value.modelKey : undefined;

  const facts: WorkspaceFacts = useMemo(
    () => ({
      status: application.status,
      appliedOn: formatTrailDate(application.date_applied ?? undefined, { now }),
      postingUrl,
      generation: generationFact({ read: controller.read, state: controller.state, hasResume: controller.hasResume, downloaded: controller.downloaded }),
      gmail: gmailFactOf(gmail, credentials.kind === "ready" ? credentials.value : null),
    }),
    [application.status, application.date_applied, now, postingUrl, controller.read, controller.state, controller.hasResume, controller.downloaded, gmail, credentials],
  );

  const input: NextStepInput = useMemo(
    () => ({ page: "workspace", signedIn: true, profile: profileCheck, credentials: credentialsCheck, pageFailure: null, facts }),
    [profileCheck, credentialsCheck, facts],
  );
  const { outcome, rowId } = useMemo(() => nextStepWithRow(input), [input]);

  // The handlers the band's buttons name (lib/nextStep/pages/workspace.ts). Each is the handler of the
  // button the same step has in the body, so the band holds the step and the body keeps a copy.
  function focusHonestRead() {
    const heading = document.getElementById(HONEST_READ_HEADING_ID);
    if (heading === null) return;
    heading.scrollIntoView({ block: "start" });
    heading.focus({ preventScroll: true });
  }

  function onBandAction(intent: string) {
    switch (intent) {
      case WORKSPACE_INTENTS.generate:
        setRefocus(true);
        void controller.generate();
        return;
      case WORKSPACE_INTENTS.generateAnyway:
        setRefocus(true);
        void controller.generate(true);
        return;
      case WORKSPACE_INTENTS.downloadPdf:
        setRefocus(true);
        void controller.downloadResume();
        return;
      case WORKSPACE_INTENTS.readFit:
        focusHonestRead();
        return;
      case "retry-setup":
        onRetrySetup();
        return;
      case "retry":
        onReload();
        return;
    }
  }

  // The step the person pressed is replaced by the next one; when the control that had focus is gone,
  // focus goes back to the band. It waits until nothing the band depends on is still out.
  const busy = controller.state.kind === "generating" || controller.downloading !== null;
  useEffect(() => {
    if (!refocus || busy) return;
    setRefocus(false);
    const active = document.activeElement;
    if (active !== null && active !== document.body) return;
    document.getElementById(NEXT_STEP_FOCUS_ID)?.focus();
  });

  // A link that says "see why" (the band on Today, a deep link) arrives at #generate. When the latest
  // attempt was held back, the person is there for the honest read, not for the top of the section.
  const heldBack = controller.state.kind === "ready" && controller.state.result.resume === null && !!controller.state.result.fit;
  const focusedFor = useRef<string | null>(null);
  useEffect(() => {
    if (location.hash !== `#${SECTION_IDS.generate}`) {
      focusedFor.current = null;
      return;
    }
    if (!heldBack || focusedFor.current === location.key) return;
    const heading = document.getElementById(HONEST_READ_HEADING_ID);
    if (heading === null) return;
    focusedFor.current = location.key;
    heading.focus();
  }, [location.hash, location.key, heldBack]);

  // A link to #hiring-posts that arrives before the server has said whether the feature is on lands on
  // an empty anchor; scroll again once it has answered, when the section (or its note) exists.
  useScrollToHash(location.hash === `#${SECTION_IDS.hiringPosts}` && hiring.kind !== "checking", location.hash, hiring.kind);

  const pending = statusBusy
    ? { intent: null }
    : controller.state.kind === "generating"
      ? { intent: WORKSPACE_INTENTS.generate, label: "Generating..." }
      : controller.downloading === "resume"
        ? { intent: WORKSPACE_INTENTS.downloadPdf, label: "Compiling PDF..." }
        : null;

  const coverLetterInBand = rowId === "A1" || rowId === "A1g";
  const setup = modelSetupOf(modelKey);
  const result = controller.state.kind === "ready" ? controller.state.result : null;
  const drafted = result !== null && result.resume !== null;
  const summary = stateSummary({
    status: application.status,
    hasResume: controller.hasResume,
    latest: drafted ? "drafted" : heldBack ? "held_back" : null,
    atsScore: drafted ? (latestAttempt(result.ats_attempts)?.overall_score ?? null) : null,
    flagged: facts.generation.state === "drafted" ? facts.generation.flagged : null,
  });
  const showSubmit = application.status === "saved";

  return (
    <>
      <WorkspaceHeader
        application={application}
        now={now}
        statusBusy={statusBusy}
        onStatus={(status) => onStatus(status, "select")}
        problem={statusProblem?.source === "select" ? <FailureState message={statusProblem.message} onRetry={statusProblem.retry} /> : undefined}
      />
      <NextStepBand outcome={outcome} onAction={onBandAction} pending={pending}>
        {coverLetterInBand && (
          <label className="bj-ws-check bj-small">
            <input type="checkbox" checked={controller.wantCoverLetter} onChange={(e) => controller.setWantCoverLetter(e.target.checked)} disabled={controller.state.kind === "generating"} />
            Also draft a cover letter (a real, extra model call)
          </label>
        )}
      </NextStepBand>
      <JumpBar groups={workspaceJumpGroups(hiring.kind === "enabled")} ready />
      <DocumentLayout summary={<p>{summary}</p>}>
        <ResumeSections
          applicationId={id}
          descriptionText={snapshot?.description_text}
          profile={profile}
          modelSetup={setup}
          generate={{
            controller,
            retryInBand: rowId === "A1c",
            coverLetterInBand,
            onParked: onReload,
          }}
        />
        <ReviewSection
          applicationId={id}
          controller={controller}
          submit={
            showSubmit
              ? {
                  postingUrl,
                  busy: statusBusy,
                  problem: statusProblem?.source === "submit" ? statusProblem.message : null,
                  onMarkApplied: () => onStatus("applied", "submit"),
                }
              : null
          }
        />
        <PanelSections applicationId={id} hiring={hiring} hiringRequested={location.hash === `#${SECTION_IDS.hiringPosts}`} />
      </DocumentLayout>
    </>
  );
}
