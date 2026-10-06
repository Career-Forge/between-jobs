import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { LANDING_PATH, LOGIN_PATH, REGISTER_PATH } from "../lib/publicRoutes";
import { LegalLinks } from "./LegalLinks";

// What a signed-out visitor sees around the landing page and the two legal pages: a header with
// the way in, the page itself as the one <main>, and a footer with the legal links, the source
// and the two contact addresses. No hooks, so a test can render it directly.

export function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <div className="bj-public">
      <header className="bj-public-header">
        <Link to={LANDING_PATH} className="bj-public-wordmark">
          Between Jobs
        </Link>
        <nav className="bj-public-actions" aria-label="Account">
          <Link to={LOGIN_PATH}>Sign in</Link>
          <Link to={REGISTER_PATH} className="bj-button-link bj-button-link-primary">
            Create account
          </Link>
        </nav>
      </header>
      <main className="bj-public-main">{children}</main>
      <footer className="bj-public-footer">
        <LegalLinks />
        <p className="bj-muted bj-small">
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
            Source code
            <span className="bj-visually-hidden"> (opens in a new tab)</span>
          </a>
        </p>
        <p className="bj-muted bj-small">
          Questions and bug reports: <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          <br />
          Privacy requests: <a href={`mailto:${PRIVACY_EMAIL}`}>{PRIVACY_EMAIL}</a>
        </p>
      </footer>
    </div>
  );
}
