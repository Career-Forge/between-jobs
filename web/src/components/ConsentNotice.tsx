import { Link } from "react-router-dom";
import { PRIVACY_PATH, TERMS_PATH } from "../lib/publicRoutes";

// The one line on the sign-in page, in both of its forms: "Create account" makes an account, and
// so does "Continue with Google" for someone who has none, whichever form they are looking at.
// It informs; it does not capture or block anything (recording which version a person agreed to
// is a later task). The links open in a new tab so the half-filled form is still there when the
// person comes back.

export function ConsentNotice() {
  return (
    <p className="bj-muted bj-small bj-consent-notice">
      By creating an account or continuing with Google you agree to the{" "}
      <Link
        to={TERMS_PATH}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="Terms (opens in a new tab)"
      >
        Terms
      </Link>{" "}
      and acknowledge the{" "}
      <Link
        to={PRIVACY_PATH}
        target="_blank"
        rel="noopener noreferrer"
        aria-label="Privacy Policy (opens in a new tab)"
      >
        Privacy Policy
      </Link>
      .
    </p>
  );
}
