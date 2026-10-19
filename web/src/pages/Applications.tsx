import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { ApplicationsView, type ApplicationsLayout, type ApplicationsListState } from "../components/applications/ApplicationsView";
import { nameOf, type MoveProblem } from "../components/applications/ApplicationParts";
import { START_TRACKING_HEADING_ID, START_TRACKING_ID } from "../components/applications/TrackForms";
import { NEXT_STEP_FOCUS_ID } from "../components/paper/NextStepBand";
import { apiFetch } from "../lib/api";
import { STATUS_LABELS, statusCounts } from "../lib/applicationsBoard";
import type { Application, ApplicationStatus } from "../lib/applicationsTypes";
import { phoneStatus, readStoredStatus, storeStatus } from "../lib/applicationsView";
import { browserStorage } from "../lib/firstRun";
import { nextStepWithRow, type NextStepInput } from "../lib/nextStep";
import { START_TRACKING_INTENT, type ApplicationsFacts } from "../lib/nextStep/pages/applications";
import { setupChecks } from "../lib/setupReads";
import { formatTrailTime } from "../lib/trail";
import { useIsPhone } from "../lib/useMediaQuery";
import { useScrollToHash } from "../lib/useScrollToHash";
import { useSetupReads } from "../lib/useSetupReads";

// Applications: every job the person tracks, with its status, as a list, a board, or -- on a phone --
// one status at a time. This file is the reading and the requests; what is drawn for each state is
// components/applications/ApplicationsView.tsx, and what the next-step band says is
// lib/nextStep/pages/applications.ts.
//
// Requests. GET /applications is the page's own data: when it fails the band says so (row F1) and
// offers Try again. GET /profile/current and GET /credentials are read once (lib/useSetupReads.ts)
// because the band's setup rows need them. A status change is POST /applications/{id}/stage, followed
// by a re-read of the whole list (no optimistic merge of a half-known row). Creating one is
// POST /applications (a pasted posting) or POST /applications/from-url, each followed by the same
// re-read. A failed move is kept on its own card with a Retry that sends it again under the same
// idempotency key; it is dropped as soon as a read shows the card on a different status than it had
// when the move failed, because a request that failed on the wire may have gone through.
//
// A Set of busy ids, not a single id: the board can have several cards in flight at once (a drag on
// one card while another card's select is still awaiting its response), and one shared busy id would
// let the earlier request's card read as no longer busy the moment a second one starts, re-enabling
// it for a concurrent, racing stage change.

// Where focus returns to when the control that had it is gone: the band, a card's status control
// (after a move), or a card's Retry (after a Retry that failed again).
type FocusRequest = { kind: "band" } | { kind: "card"; id: string } | { kind: "problem"; id: string };

interface LoadFailure {
  message: string;
  /** The client time it failed, as the band's why line writes it: "09:14". */
  at: string;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Takes the person to the paste form: the one way in that needs no key.
function revealTracking(): void {
  const target = document.getElementById(START_TRACKING_ID);
  if (target === null) return;
  target.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  document.getElementById(START_TRACKING_HEADING_ID)?.focus({ preventScroll: true });
}

export default function Applications() {
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null);
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [problems, setProblems] = useState<Record<string, MoveProblem | undefined>>({});
  const [layout, setLayout] = useState<ApplicationsLayout>("list");
  const [remembered, setRemembered] = useState<ApplicationStatus | null>(() => readStoredStatus(browserStorage()));
  // `count` changes with every announcement, so the same words said twice in a row (moving a card
  // out and back) still change what the live region holds and are read again.
  const [announcement, setAnnouncement] = useState({ text: "", count: 0 });
  const [refocus, setRefocus] = useState<FocusRequest | null>(null);
  const [now] = useState(() => new Date());
  const phone = useIsPhone();
  const location = useLocation();
  const setup = useSetupReads();
  const checks = useMemo(() => setupChecks(setup.reads, setup.at, { modelKey: true }), [setup.reads, setup.at]);
  const retrySetup = setup.retry;
  const inFlight = useRef(new Set<string>());
  // The setup reads as they were when "Try again" was pressed on them: their answer is the first
  // different one that is not still loading, and focus returns to the band then.
  const setupRetry = useRef<typeof setup.reads | null>(null);
  const latest = useRef<Application[] | null>(null);
  latest.current = applications;

  const load = useCallback(async (): Promise<boolean> => {
    try {
      const rows = await apiFetch<Application[]>("/applications");
      setApplications(rows);
      setLoadFailure(null);
      return true;
    } catch (e) {
      setLoadFailure({
        message: e instanceof Error ? e.message : "Failed to load applications",
        at: formatTrailTime(new Date().toISOString()) ?? "",
      });
      return false;
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // A link to /applications#start-tracking lands on the paste form. The forms are always on the page,
  // so what the hash names exists from the first render.
  useScrollToHash(true, location.hash);

  // `idempotencyKey` is only passed by a Retry, which sends the failed move again under its own key.
  async function changeStatus(applicationId: string, newStatus: ApplicationStatus, idempotencyKey: string = crypto.randomUUID()) {
    if (inFlight.current.has(applicationId)) return;
    inFlight.current.add(applicationId);
    const application = latest.current?.find((candidate) => candidate.id === applicationId);
    setBusyIds((current) => new Set(current).add(applicationId));
    setProblems((current) => ({ ...current, [applicationId]: undefined }));
    try {
      try {
        await apiFetch(`/applications/${applicationId}/stage`, {
          method: "POST",
          body: JSON.stringify({
            new_status: newStatus,
            idempotency_key: idempotencyKey,
          }),
        });
      } catch (e) {
        // The card stays on its old status with the reason under it; the rest of the page is as it was.
        setProblems((current) => ({
          ...current,
          [applicationId]: {
            to: newStatus,
            from: application?.status ?? null,
            idempotencyKey,
            message: e instanceof Error ? e.message : "Failed to change status",
          },
        }));
        // A Retry that failed again replaced the button that was pressed with a new one.
        setRefocus({ kind: "problem", id: applicationId });
        return;
      }
      const read = await load();
      const text = read ? `Moved ${application === undefined ? "the application" : nameOf(application)} to ${STATUS_LABELS[newStatus]}.` : "";
      setAnnouncement((previous) => ({ text, count: previous.count + 1 }));
      setRefocus({ kind: "card", id: applicationId });
    } finally {
      inFlight.current.delete(applicationId);
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(applicationId);
        return next;
      });
    }
  }

  function retryMove(applicationId: string) {
    const problem = problems[applicationId];
    if (problem !== undefined) void changeStatus(applicationId, problem.to, problem.idempotencyKey);
  }

  async function retryLoad() {
    // The first read has nothing to show while it is out, so it goes back to loading; a later read
    // keeps the cards that were read and the failure until the answer is in.
    if (latest.current === null) setLoadFailure(null);
    await load();
    // The button that was pressed is gone with the failure it answered: focus goes to the band.
    setRefocus({ kind: "band" });
  }

  function onBandAction(intent: string) {
    switch (intent) {
      case "retry":
        void retryLoad();
        return;
      case "retry-setup":
        setupRetry.current = setup.reads;
        retrySetup();
        // When the whole API was down the list failed too, and the body has no Retry of its own (the
        // band holds the only one), so a single press reads both.
        if (loadFailure !== null) void retryLoad();
        return;
      case START_TRACKING_INTENT:
        revealTracking();
        return;
    }
  }

  const facts: ApplicationsFacts = useMemo(
    () => (applications === null ? { list: { state: "loading" } } : { list: { state: "ready", counts: statusCounts(applications) } }),
    [applications],
  );
  const input: NextStepInput = useMemo(
    () => ({
      page: "applications",
      signedIn: true,
      profile: checks.profile,
      credentials: checks.credentials,
      pageFailure:
        loadFailure === null ? null : { what: "Couldn't load your applications", call: "GET /applications", at: loadFailure.at, intent: "retry" },
      facts,
    }),
    [checks, loadFailure, facts],
  );
  const { outcome } = useMemo(() => nextStepWithRow(input), [input]);

  useEffect(() => {
    const before = setupRetry.current;
    if (before === null || setup.reads === before || setup.reads.profile.kind === "loading" || setup.reads.credentials.kind === "loading") return;
    setupRetry.current = null;
    setRefocus({ kind: "band" });
  }, [setup.reads]);

  // Moves focus to where the person's place went: the band after "Try again" replaced its button, or
  // the card they just moved (a board card that changes column is a new element). It waits until the
  // band has settled and leaves focus alone when it is anywhere real.
  const settled = outcome.status !== "loading";
  useEffect(() => {
    if (refocus === null || !settled) return;
    setRefocus(null);
    const active = document.activeElement;
    if (active !== null && active !== document.body) return;
    if (refocus.kind === "band") {
      document.getElementById(NEXT_STEP_FOCUS_ID)?.focus();
      return;
    }
    const card = [...document.querySelectorAll<HTMLElement>("[data-app-id]")].find((element) => element.dataset.appId === refocus.id);
    const retry = refocus.kind === "problem" ? card?.querySelector<HTMLElement>(".bj-app-problem button") : null;
    (retry ?? card?.querySelector<HTMLElement>('[data-role="status"]') ?? document.querySelector<HTMLElement>('[data-role="picker"]'))?.focus();
  });

  const list: ApplicationsListState =
    applications !== null
      ? { kind: "ready", applications, refreshFailed: loadFailure?.message ?? null }
      : loadFailure !== null
        ? { kind: "failed", message: loadFailure.message }
        : { kind: "loading" };

  // A problem is about the card as it was when the move failed. Once a read shows the card on any
  // other status (the move did go through, or it changed elsewhere), or no longer shows it, the
  // problem is not about this card any more.
  const shownProblems = useMemo(() => {
    const kept: Record<string, MoveProblem | undefined> = {};
    for (const [id, problem] of Object.entries(problems)) {
      if (problem === undefined) continue;
      const application = applications?.find((candidate) => candidate.id === id);
      if (application !== undefined && application.status === problem.from) kept[id] = problem;
    }
    return kept;
  }, [problems, applications]);

  const shownStatus = phoneStatus(remembered, statusCounts(applications ?? []));

  return (
    <ApplicationsView
      now={now}
      outcome={outcome}
      onBandAction={onBandAction}
      list={list}
      phone={phone}
      layout={layout}
      onLayoutChange={setLayout}
      phoneStatus={shownStatus}
      onPhoneStatusChange={(status) => {
        setRemembered(status);
        storeStatus(browserStorage(), status);
      }}
      busyIds={busyIds}
      problems={shownProblems}
      onChangeStatus={(applicationId, status) => void changeStatus(applicationId, status)}
      onRetryMove={retryMove}
      onCreated={async () => {
        await load();
      }}
      onTrack={revealTracking}
      announcement={announcement.text + (announcement.count % 2 === 0 ? "" : "\u00a0")}
      onRetryList={() => void retryLoad()}
    />
  );
}
