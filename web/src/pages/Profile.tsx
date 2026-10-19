import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { AccountCard } from "../components/AccountCard";
import { NextStepBand } from "../components/paper/NextStepBand";
import { BlockTrail, DocumentBlock, DocumentLayout } from "../components/paper/DocumentLayout";
import { PageHeader } from "../components/paper/PageHeader";
import { EmptyState, FailureState, LoadingState } from "../components/paper/StateViews";
import { ActiveProfile } from "../components/profile/ActiveProfile";
import { ImportSection } from "../components/profile/JsonImport";
import { JsonReview } from "../components/profile/JsonReview";
import { ResumeImportCard, type ResumeImportHandle, type ReviewSummary } from "../components/ResumeImportCard";
import { ALREADY_ACTIVE_NOTICE } from "../components/ResumeImportView";
import { TesterProgrammeLine } from "../components/TesterProgrammeLine";
import { ApiError, apiFetch } from "../lib/api";
import { modelKeyOf, type ModelKeyReading } from "../lib/modelKey";
import { nextStepWithRow, type NextStepInput, type SetupCheck } from "../lib/nextStep";
import {
  IMPORT_REVIEW_ID,
  PROFILE_INTENTS,
  SET_UP_ID,
  profileOutcome,
  type ProfilePageFacts,
  type WaitingImport,
} from "../lib/nextStep/pages/profile";
import { activeSince, jsonReviewTrail, profileSummary } from "../lib/profileTrails";
import type { ProfileVersion } from "../lib/profileTypes";
import { SETTINGS_PATH } from "../lib/settingsPaths";
import { formatTrailTime } from "../lib/trail";
import { useScrollToHash } from "../lib/useScrollToHash";
import "../styles/profile.css";

// Profile: the person's structured profile as a document -- who it is, their personal details, the
// resume layout defaults, the sections (each editable), importing a new version and deleting the
// account. The profile is what every resume draft, score and practice question is built from, so this is
// also the page where setup starts.
//
// Requests (the page's own data is GET /profile/current; the rest are as they always were):
//   GET /profile/current         the active version. A 404 is "no profile yet"; anything else is a
//                                failure of the page's own data, which the band says (row G1) and the body
//                                names. A re-read after activate, cancel or an edit never returns to
//                                Loading: the page stays as it was while the control that acted is busy.
//   GET /credentials             read once, for the band's "no usable model key" row (G3) only
//   POST /profile/versions       the JSON import: a pending version, parsed by code, to review
//   POST .../{id}/activate       make a version the active profile (the JSON preview's button, and the
//                                resume file card's confirm -- components/ResumeImportCard.tsx)
//   DELETE .../{id}              cancel a pending version
// The resume-file import (a PDF or DOCX read by the person's own model) is ResumeImportCard; the header,
// section order and settings cards read and save the resume document themselves.
//
// A WAITING IMPORT IS KNOWN ONLY IN THIS SESSION. POST /profile/versions returns the draft; no call lists a
// waiting one, and GET /profile/current returns only the active version. After a reload there is no draft
// to review (the band says the profile is active) and the page never draws "import waiting" from memory.
//
// THE NEXT-STEP BAND holds the page's one cyan button. With an active profile and nothing waiting it is
// unlit ("Your profile is active"); with an import waiting it links to the review and then holds
// "Confirm and make active". Every other button on the page, including the ones that would do the same
// thing, is outlined. Which row says what is lib/nextStep/pages/profile.ts.
//
// THE JSON PREVIEW REPLACES THE PAGE'S BODY (as it always has): showing it beside the layout cards would
// leave an edit possible against a profile that is about to be replaced. A resume-file import is held
// apart from it both ways (`fileImportOpen`, `blocked`), since the preview would drop that review unseen.

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string; at: string }
  | { kind: "none" }
  | { kind: "active"; version: ProfileVersion }
  | { kind: "pending"; version: ProfileVersion; hadActive: boolean };

// What GET /credentials said, as far as the band needs it: whether some model key is usable.
type Credentials = { kind: "loading" } | { kind: "failed"; at: string } | { kind: "ready"; modelKey: ModelKeyReading };

// The client's own wall-clock time as the band's failure line writes it: "09:14".
function clockTime(now: Date): string {
  return formatTrailTime(now.toISOString()) ?? "";
}

export default function Profile() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [credentials, setCredentials] = useState<Credentials>({ kind: "loading" });
  // What the page's own request is doing, so the band and the buttons say the true thing: the JSON import,
  // an activation, or the cancel of a draft. At most one runs at a time.
  const [working, setWorking] = useState<"import" | "activate" | "cancel" | null>(null);
  const busy = working !== null;
  // Each failure and note is drawn where the person acted and nowhere else: a refused import under the
  // import section, a failed activation or cancel under the review, and the note that a pasted JSON
  // matched the profile in use, also under the import section.
  const [importError, setImportError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  // A resume-file import is under way (a file being read, a draft waiting to be decided). The
  // JSON import waits for it: its preview replaces the whole card list below, which would drop the
  // file draft's review with no word. The reverse holds too (`blocked={busy}`), so the two never run
  // at once, and the preview branch below is never reached with a file import open.
  const [fileImportOpen, setFileImportOpen] = useState(false);
  // What is true of a resume file's draft that is waiting for a decision, or null.
  const [fileReview, setFileReview] = useState<ReviewSummary | null>(null);
  // A resume file's draft arriving ends the story of any refused or matching JSON import above it.
  const onFileReviewChange = useCallback((review: ReviewSummary | null) => {
    setFileReview(review);
    if (review !== null) {
      setImportError(null);
      setImportNotice(null);
    }
  }, []);
  const importCard = useRef<ResumeImportHandle>(null);
  // The JSON preview was just left (activated, or cancelled): the control that had focus is gone, so focus
  // goes to where the person's place went once the page shows what the read answered.
  const [leftReview, setLeftReview] = useState(false);
  const [now] = useState(() => new Date());
  const location = useLocation();

  const loadCurrent = useCallback(async () => {
    try {
      const version = await apiFetch<ProfileVersion>("/profile/current");
      setState({ kind: "active", version });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setState({ kind: "none" });
      } else {
        setState({ kind: "error", message: e instanceof Error ? e.message : "Failed to load", at: clockTime(new Date()) });
      }
    }
  }, []);

  const loadCredentials = useCallback(async () => {
    try {
      const rows = await apiFetch<Record<string, unknown>[]>("/credentials");
      setCredentials({ kind: "ready", modelKey: modelKeyOf(Array.isArray(rows) ? rows : []) });
    } catch {
      setCredentials({ kind: "failed", at: clockTime(new Date()) });
    }
  }, []);

  useEffect(() => {
    void loadCurrent();
    void loadCredentials();
  }, [loadCurrent, loadCredentials]);

  async function importJson(rawText: string) {
    if (fileImportOpen) return;
    setWorking("import");
    setImportError(null);
    setReviewError(null);
    setImportNotice(null);
    const hadActive = state.kind === "active";
    const activeId = state.kind === "active" ? state.version.id : null;
    try {
      const version = await apiFetch<ProfileVersion>("/profile/versions", {
        method: "POST",
        body: JSON.stringify({ raw_text: rawText }),
      });
      // The importer returns an existing version with the same content as it is, so text identical to the
      // profile in use comes back as that very version -- already active, with nothing to review. (A
      // replaced version of the same content is not this: it is a draft that can be made active again.)
      if (activeId !== null && version.id === activeId) {
        setImportNotice(ALREADY_ACTIVE_NOTICE);
      } else {
        setState({ kind: "pending", version, hadActive });
      }
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setWorking(null);
    }
  }

  async function activate(versionId: string) {
    setWorking("activate");
    setReviewError(null);
    try {
      await apiFetch(`/profile/versions/${versionId}/activate`, { method: "POST" });
      setLeftReview(true);
      await loadCurrent();
    } catch (e) {
      setReviewError(e instanceof Error ? e.message : "Activation failed");
    } finally {
      setWorking(null);
    }
  }

  // A cancel the server says is already moot -- the version is gone (NOT_FOUND) or was already
  // activated (CONFLICT) -- reloads the truth. Any other failure (the network, a 5xx) leaves the review
  // up with its words, and pressing Cancel again is the retry.
  async function cancel(versionId: string) {
    setWorking("cancel");
    setReviewError(null);
    try {
      await apiFetch(`/profile/versions/${versionId}`, { method: "DELETE" });
    } catch (e) {
      if (!(e instanceof ApiError && (e.code === "NOT_FOUND" || e.code === "CONFLICT"))) {
        setReviewError(e instanceof Error ? e.message : "Cancel failed");
        setWorking(null);
        return;
      }
    }
    setLeftReview(true);
    await loadCurrent();
    setWorking(null);
  }

  // ── the band ───────────────────────────────────────────────────────────

  const active = state.kind === "active" || (state.kind === "pending" && state.hadActive);
  const waiting: WaitingImport | null =
    state.kind === "pending"
      ? { source: "json" }
      : fileReview === null
        ? null
        : { source: "file", found: fileReview.found, leftOut: fileReview.leftOut, remaining: fileReview.remaining };
  // The person is on the review when it is the page's main content: the JSON preview replaces the
  // body, and with no profile in use the file review is all there is. Otherwise they got there by the
  // band's link to #import-review.
  const onReview = waiting !== null && (state.kind === "pending" || !active || location.hash === `#${IMPORT_REVIEW_ID}`);
  const facts: ProfilePageFacts = {
    waiting,
    onReview,
    activeSince: state.kind === "active" ? activeSince(state.version, now) : null,
    replacesCurrent: active,
  };

  const input: NextStepInput = {
    page: "profile",
    signedIn: true,
    profile: profileCheck(state, active, waiting !== null),
    credentials:
      credentials.kind === "loading"
        ? { state: "loading" }
        : credentials.kind === "failed"
          ? { state: "failed", call: "GET /credentials", at: credentials.at }
          : { state: "ready", value: { modelKey: credentials.modelKey } },
    facts,
  };
  const outcome = profileOutcome(nextStepWithRow(input), facts);

  // Reads again only what failed. Re-reading the profile when only the keys failed would put the page back
  // to the profile or to "none" and drop a JSON preview that is waiting, which nothing can list again.
  function retrySetup() {
    if (state.kind === "error") {
      setState({ kind: "loading" });
      void loadCurrent();
    }
    if (credentials.kind === "failed") void loadCredentials();
  }

  function onBandAction(intent: string) {
    switch (intent) {
      case "retry-setup":
        retrySetup();
        return;
      case PROFILE_INTENTS.useImport:
        importCard.current?.confirmAndUse();
        return;
      case PROFILE_INTENTS.activateJson:
        if (state.kind === "pending") void activate(state.version.id);
        return;
    }
  }

  // The band's button is the JSON preview's while one is open, so it names what the page is doing with the
  // draft: making it active, or throwing it away.
  const activating =
    state.kind === "pending" && working === "activate"
      ? { intent: PROFILE_INTENTS.activateJson, label: "Activating..." }
      : state.kind === "pending" && working === "cancel"
        ? { intent: PROFILE_INTENTS.activateJson, label: "Cancelling..." }
        : fileReview?.using
          ? { intent: PROFILE_INTENTS.useImport, label: "Activating..." }
          : null;

  // A hash that names the import section or the review (the band's links, the setup checklist's) scrolls
  // there and puts focus on its heading once the page has what it points at. The review can arrive after
  // the hash is already in the address, so its arrival is a reason to look again.
  // The location's key is a reason too: following a link to the hash already in the address changes the key
  // and not the hash, and it should scroll again.
  useScrollToHash(state.kind !== "loading", location.hash, waiting !== null, state.kind, location.key);

  // Focus after the JSON preview goes: the profile's name when there is one, else the import section's first heading.
  useEffect(() => {
    if (!leftReview || (state.kind !== "active" && state.kind !== "none")) return;
    setLeftReview(false);
    const target = state.kind === "active" ? document.getElementById("bj-profile-name") : document.querySelector<HTMLElement>(`#${SET_UP_ID} h2`);
    target?.focus();
  }, [leftReview, state.kind]);

  const summary = state.kind === "active" ? <SummaryLines lines={profileSummary(state.version, now, waiting !== null)} /> : undefined;

  return (
    <div className="bj-profile">
      <PageHeader
        title="Profile"
        subtitle="What every resume draft, score and practice question is built from. Read from your saved profile and resume settings."
        actions={
          <Link className="bj-button-link" to={SETTINGS_PATH}>
            Settings
          </Link>
        }
      />
      <NextStepBand outcome={outcome} onAction={onBandAction} pending={activating} />

      {state.kind === "loading" && <LoadingState rows={4} rowHeight={96} label="Loading your profile..." />}

      {state.kind === "error" && (
        <>
          <FailureState
            message={`Couldn't read your profile (${state.message}). You didn't do anything wrong, and nothing was lost.`}
          />
          <TesterProgrammeLine />
          <AccountCard />
        </>
      )}

      {state.kind === "pending" && (
        <DocumentLayout summary={summary} measure="wide">
          <DocumentBlock id="json-review">
            <JsonReview
              version={state.version}
              busy={busy}
              error={reviewError}
              onActivate={() => void activate(state.version.id)}
              onCancel={() => void cancel(state.version.id)}
              trail={<JsonReviewTrail version={state.version} now={now} />}
            />
          </DocumentBlock>
          <DocumentBlock id="account">
            <TesterProgrammeLine />
            <AccountCard />
          </DocumentBlock>
        </DocumentLayout>
      )}

      {(state.kind === "none" || state.kind === "active") && (
        <DocumentLayout summary={summary} measure="wide">
          {state.kind === "active" && <ActiveProfile version={state.version} onEdited={loadCurrent} now={now} />}
          {state.kind === "none" && (
            <EmptyState heading="Your profile isn't set up yet">
              <p>
                Once it is, it shows here as Experience, Projects, Education and the other sections, and every resume draft, score
                and practice question is built from it.
              </p>
            </EmptyState>
          )}
          <div id={state.kind === "none" ? SET_UP_ID : "new-version"} className="bj-import-group">
            <DocumentBlock id="resume-import">
              <ResumeImportCard
                ref={importCard}
                replacesCurrent={state.kind === "active"}
                onActivated={() => {
                  setImportError(null);
                  setImportNotice(null);
                  void loadCurrent();
                }}
                blocked={busy}
                onOpenChange={setFileImportOpen}
                onReviewChange={onFileReviewChange}
                modelKey={credentials.kind === "ready" ? credentials.modelKey : undefined}
              />
            </DocumentBlock>
            <DocumentBlock id="json-import">
              <ImportSection
                replacing={state.kind === "active"}
                busy={busy}
                disabled={fileImportOpen}
                error={importError}
                notice={importNotice}
                onImport={(text) => void importJson(text)}
              />
            </DocumentBlock>
          </div>
          <DocumentBlock id="account">
            <TesterProgrammeLine />
            <AccountCard />
          </DocumentBlock>
        </DocumentLayout>
      )}
    </div>
  );
}

// What the global rows are told about the profile: whether the read worked, whether a version is in
// use, and whether an import is waiting.
function profileCheck(state: State, active: boolean, importWaiting: boolean): SetupCheck<{ active: boolean; importWaiting: boolean }> {
  if (state.kind === "loading") return { state: "loading" };
  if (state.kind === "error") return { state: "failed", call: "GET /profile/current", at: state.at };
  return { state: "ready", value: { active, importWaiting } };
}

function SummaryLines({ lines }: { lines: string[] }) {
  return <p>{lines.join(" · ")}</p>;
}

function JsonReviewTrail({ version, now }: { version: ProfileVersion; now: Date }) {
  const spec = jsonReviewTrail(version, now);
  return <BlockTrail trail={spec.trail} receipt={spec.receipt} receiptTitle={spec.receiptTitle} />;
}
