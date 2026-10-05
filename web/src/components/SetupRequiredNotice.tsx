import { Link } from "react-router-dom";
import type { Problem, SetupNotice } from "../lib/setupRequired";

// A SETUP_REQUIRED reply on screen: the server's sentence and, when it pointed at a page
// the app routes, one link to it. What the link is, and whether there is one, is decided
// by lib/setupRequired.ts; this only draws it. Setup-needed is a to-do rather than a
// failure, so it is the gold callout (the same look as the Hiring posts one), not the
// red of an error.
export function SetupRequiredNotice({ notice }: { notice: SetupNotice }) {
  return (
    <div className="bj-setup-notice" role="alert">
      <div>{notice.message}</div>
      {notice.linkTo !== null && notice.linkLabel !== null && (
        <Link to={notice.linkTo}>{notice.linkLabel}</Link>
      )}
    </div>
  );
}

// For the places that keep one slot for "what went wrong": a plain message is the red
// error line it always was, a setup notice is the callout above, nothing is nothing.
export function ProblemView({ problem }: { problem: Problem | null }) {
  if (problem === null || problem === "") return null;
  if (typeof problem === "string") return <div className="bj-error">{problem}</div>;
  return <SetupRequiredNotice notice={problem} />;
}
