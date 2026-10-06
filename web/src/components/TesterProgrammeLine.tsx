import { useEffect } from "react";
import { Link } from "react-router-dom";
import { ENROLL_PATH } from "../lib/publicRoutes";
import {
  PROFILE_LINE_HEADING,
  PROFILE_LINE_LINK,
  profileLineStatus,
} from "../lib/enrollment";
import { useCapabilities } from "../lib/useCapabilities";
import { useEnrollmentController } from "../lib/useEnrollment";

// The Profile page's "Tester programme: join or withdraw" line. Shown only
// when the server requires the programme or the person is already in it, so a visitor to a server
// that has none is never offered one (profileLineStatus decides, and says nothing until it
// knows). The Profile page is exempt from the gate, so this is reachable by someone who has not
// joined yet.

export function TesterProgrammeLineView({ status }: { status: string | null }) {
  if (status === null) return null;
  return (
    <div className="bj-card bj-tester-line">
      <h2>{PROFILE_LINE_HEADING}</h2>
      <p>{status}</p>
      <p>
        <Link to={ENROLL_PATH}>{PROFILE_LINE_LINK}</Link>
      </p>
    </div>
  );
}

export function TesterProgrammeLine() {
  const controller = useEnrollmentController();
  const capabilities = useCapabilities();
  const ensureLoaded = controller?.ensureLoaded;

  useEffect(() => {
    ensureLoaded?.();
  }, [ensureLoaded]);

  if (controller === null) return null;
  return <TesterProgrammeLineView status={profileLineStatus(capabilities, controller.load)} />;
}
