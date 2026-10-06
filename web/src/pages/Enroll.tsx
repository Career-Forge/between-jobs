import { useEffect, useReducer, useRef } from "react";
import {
  EnrollmentLoadingView,
  EnrollmentRetryView,
  EnrollmentView,
} from "../components/EnrollmentView";
import { apiFetch } from "../lib/api";
import { isTesterProgramRequired } from "../lib/capabilities";
import {
  enrollPageReducer,
  firstInvalidFieldId,
  focusTargetAfterChange,
  initialEnrollPageState,
  isEnrollFormValid,
  submitEnrollment,
  validateEnrollForm,
  withdrawEnrollment,
  type FocusFacts,
} from "../lib/enrollment";
import { useCapabilities } from "../lib/useCapabilities";
import { useEnrollmentController } from "../lib/useEnrollment";

// /enroll -- join or leave the tester programme. Thin glue over the tested
// pieces: the page state and the requests are lib/enrollment.ts, the drawing is
// components/EnrollmentView.tsx, and where the person stands is the shared state the shell's gate
// holds (lib/useEnrollment.ts), so a join here opens the rest of the app at once.
//
// The gate also draws this page in place of any other when the server requires the programme and
// the person has not joined. A ref, not the busy state, guards the double submit: state only
// updates on the next render, and a second click can land before it.
//
// FOCUS. The button the person just used goes away (Withdraw turns into a confirmation, "Yes,
// withdraw" and the join button are replaced by the result), and a removed element drops keyboard
// focus to the document, so a keyboard or screen-reader user would start again from the top. After
// each such change focus moves to what replaced it (lib/enrollment.ts: focusTargetAfterChange).
// A failure is separate: the error banner takes focus itself, below.
export default function Enroll() {
  const controller = useEnrollmentController();
  const capabilities = useCapabilities();
  const [page, dispatch] = useReducer(enrollPageReducer, initialEnrollPageState);
  const inFlight = useRef(false);
  const errorRef = useRef<HTMLDivElement>(null);
  const focusFacts = useRef<FocusFacts>({
    confirmingWithdraw: page.confirmingWithdraw,
    notice: page.notice,
  });
  const ensureLoaded = controller?.ensureLoaded;
  const load = controller?.load;

  useEffect(() => {
    ensureLoaded?.();
  }, [ensureLoaded]);

  // Once, when the person's own state arrives: bring back the answers of an earlier enrollment.
  useEffect(() => {
    if (load?.kind === "ready") dispatch({ type: "seed", enrollment: load.enrollment });
  }, [load]);

  useEffect(() => {
    if (page.error) errorRef.current?.focus();
  }, [page.error]);

  useEffect(() => {
    const now: FocusFacts = { confirmingWithdraw: page.confirmingWithdraw, notice: page.notice };
    const target = focusTargetAfterChange(focusFacts.current, now);
    focusFacts.current = now;
    if (target !== null) document.getElementById(target)?.focus();
  }, [page.confirmingWithdraw, page.notice]);

  // A submit that was refused for what is missing moves focus to the first field that needs it.
  useEffect(() => {
    if (page.attempts === 0) return;
    const id = firstInvalidFieldId(validateEnrollForm(page.form));
    if (id !== null) document.getElementById(id)?.focus();
  }, [page.attempts]);

  if (controller === null || load === undefined) return null;
  if (load.kind === "idle" || load.kind === "loading") return <EnrollmentLoadingView />;
  if (load.kind === "failed") {
    return (
      <EnrollmentRetryView
        message={load.message}
        programmeRequired={isTesterProgramRequired(capabilities)}
        onRetry={controller.reload}
      />
    );
  }

  async function submit() {
    if (inFlight.current || controller === null) return;
    if (!isEnrollFormValid(page.form)) {
      dispatch({ type: "submit_attempted" });
      return;
    }
    inFlight.current = true;
    dispatch({ type: "join_started" });
    const outcome = await submitEnrollment(apiFetch, page.form);
    if (outcome.ok) {
      controller.replace(outcome.enrollment);
      dispatch({ type: "joined" });
    } else {
      dispatch({ type: "join_failed", message: outcome.message });
    }
    inFlight.current = false;
  }

  async function withdraw() {
    if (inFlight.current || controller === null) return;
    inFlight.current = true;
    dispatch({ type: "withdraw_started" });
    const outcome = await withdrawEnrollment(apiFetch);
    if (outcome.ok) {
      controller.replace(outcome.enrollment);
      dispatch({ type: "withdrew" });
    } else {
      dispatch({ type: "withdraw_failed", message: outcome.message });
    }
    inFlight.current = false;
  }

  return (
    <EnrollmentView
      enrollment={load.enrollment}
      page={page}
      programmeRequired={isTesterProgramRequired(capabilities)}
      errorRef={errorRef}
      actions={{
        setRole: (value) => dispatch({ type: "role", value }),
        setSeniority: (value) => dispatch({ type: "seniority", value }),
        setSponsorship: (value) => dispatch({ type: "sponsorship", value }),
        setAgreed: (value) => dispatch({ type: "agreed", value }),
        submit: () => void submit(),
        askWithdraw: () => dispatch({ type: "withdraw_asked" }),
        cancelWithdraw: () => dispatch({ type: "withdraw_cancelled" }),
        confirmWithdraw: () => void withdraw(),
      }}
    />
  );
}
