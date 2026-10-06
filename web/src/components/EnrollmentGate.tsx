import { useEffect, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { setEnrollmentRefusalListener } from "../lib/api";
import { enrollmentGate, enrollmentTitleFor, shouldLoadEnrollment } from "../lib/enrollment";
import { refreshCapabilities, retryCapabilities, useCapabilities } from "../lib/useCapabilities";
import { useDocumentTitle } from "../lib/useDocumentTitle";
import { EnrollmentContext, useEnrollmentState } from "../lib/useEnrollment";
import Enroll from "../pages/Enroll";
import { EnrollmentRetryView } from "./EnrollmentView";

// The tester-programme gate, around the signed-in shell's routes. When the
// server requires the programme and the person has not joined, every page that is not exempt is
// replaced by the enrollment page; the decision is lib/enrollment.ts's enrollmentGate, and this
// only asks it with the facts the hooks hold and draws the answer:
//
//   pass    the page the person asked for
//   wait    a quiet "Loading..." that appears only if the answer is slow (a flash of the app,
//           taken back a moment later, would be worse than a short blank, but a long one is not
//           acceptable either: the two asks behind it have deadlines, so it ends)
//   enroll  the enrollment page, in the page's place
//   retry   the lookup failed: said plainly, with a retry (never in or out silently)
//
// It also provides the person's enrollment to everything below (the enrollment page, the Profile
// page's line), so a join or a withdrawal changes it for all of them at once. A server that does
// not require the programme never has the gate ask for anything: the decision is "pass" as soon
// as the server's settings are known.
//
// THREE WAYS THE GATE'S PICTURE IS KEPT TRUE.
//   - A failed ask for the server's settings leaves the gate open (the server still refuses what
//     it must), but not for the rest of the visit: the gate asks again when the person moves to
//     another page, a few times at most (lib/capabilitiesStore.ts holds the cap), and the answer
//     is shared with every other reader, so the Profile line cannot say "not joined" while the
//     gate says nothing is required.
//   - A refusal for enrollment from the server (403 ENROLLMENT_REQUIRED, which api.ts reports
//     here) means the picture is out of date: a withdrawal in another tab, a newer agreement, a
//     programme switched on mid-visit. The gate asks for the settings and the person's enrollment
//     again, and the decision above then puts the enrollment page where the feature was.
//   - The gate owns the document title of the shell, because only it knows when it has drawn the
//     enrollment page at some other page's address (App.tsx leaves the shell's title alone).
export function EnrollmentGate({ children }: { children: ReactNode }) {
  const capabilities = useCapabilities();
  const { pathname } = useLocation();
  const controller = useEnrollmentState();
  const { load, ensureLoaded, reload } = controller;

  useEffect(() => {
    if (shouldLoadEnrollment(capabilities, load)) ensureLoaded();
  }, [capabilities, load, ensureLoaded]);

  useEffect(() => {
    if (capabilities.kind === "unavailable") retryCapabilities();
  }, [pathname, capabilities.kind]);

  useEffect(() => {
    setEnrollmentRefusalListener(() => {
      refreshCapabilities();
      reload();
    });
    return () => setEnrollmentRefusalListener(null);
  }, [reload]);

  const decision = enrollmentGate({ capabilities, enrollment: load, pathname });
  useDocumentTitle(enrollmentTitleFor(decision, pathname));

  let body: ReactNode;
  switch (decision) {
    case "pass":
      body = children;
      break;
    case "wait":
      body = (
        <p className="bj-muted bj-gate-wait" role="status">
          Loading...
        </p>
      );
      break;
    case "enroll":
      body = <Enroll />;
      break;
    case "retry":
      body = (
        <EnrollmentRetryView
          message={load.kind === "failed" ? load.message : "We could not check your tester enrollment."}
          // The gate only draws this when the server requires the programme.
          programmeRequired
          onRetry={controller.reload}
        />
      );
      break;
  }

  return <EnrollmentContext.Provider value={controller}>{body}</EnrollmentContext.Provider>;
}
