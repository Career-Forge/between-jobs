import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { LANDING_SECTION_LINKS } from "../content/landing";
import { FONT_LICENSES_PATH, PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { LANDING_PATH, LOGIN_PATH, REGISTER_PATH } from "../lib/publicRoutes";
import { LegalLinks } from "./LegalLinks";
import { NextStepSkipLink } from "./NextStepSkipLink";
import { MAIN_CONTENT_ID, SkipLink } from "./SkipLink";
import { Wordmark } from "./Wordmark";
import "../styles/public.css";

// What a signed-out visitor sees around the landing page and the two legal pages: a header with
// the way in, the page itself as the one <main> (the skip link's target), and a footer with the
// wordmark, the legal links, the source and the two contact addresses. No hooks, so a test can render it directly.
//
// The header's Create account is an outlined link, not the filled one: the landing page's hero
// holds the page's one cyan fill, and the legal pages, which have no hero, need none.
//
// `variant="landing"` is the page that has a next-step band: it gets the in-page section links and
// a "Skip to next step" link ahead of "Skip to content". The legal pages have no band (they have no
// "now"), so they get neither.

export function PublicLayout({ children, variant = "legal" }: { children: ReactNode; variant?: "landing" | "legal" }) {
  const landing = variant === "landing";
  return (
    <div className="bj-public">
      {landing && <NextStepSkipLink />}
      <SkipLink />
      <header className="bj-public-header">
        <Link to={LANDING_PATH} className="bj-public-wordmark">
          <Wordmark />
        </Link>
        {landing && (
          <nav className="bj-public-sections" aria-label="On this page">
            {LANDING_SECTION_LINKS.map((section) => (
              <Link key={section.id} to={{ hash: `#${section.id}` }}>
                {section.label}
              </Link>
            ))}
          </nav>
        )}
        <nav className="bj-public-actions" aria-label="Account">
          <Link to={LOGIN_PATH} className="bj-public-signin">
            Sign in
          </Link>
          <Link to={REGISTER_PATH} className="bj-button-link">
            Create account
          </Link>
        </nav>
      </header>
      <main className="bj-public-main" id={MAIN_CONTENT_ID} tabIndex={-1}>
        {children}
      </main>
      <footer className="bj-public-footer">
        <div className="bj-public-footer-inner">
          <div className="bj-public-footer-brand">
            <Wordmark />
          </div>
          <div className="bj-public-footer-links">
            <LegalLinks />
            <p className="bj-muted bj-small">
              <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
                Source code
                <span className="bj-visually-hidden"> (opens in a new tab)</span>
              </a>
              {" · "}
              <a href={FONT_LICENSES_PATH} target="_blank" rel="noopener noreferrer">
                Font licenses
                <span className="bj-visually-hidden"> (opens in a new tab)</span>
              </a>
            </p>
            <p className="bj-muted bj-small">
              Questions and bug reports: <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
              <br />
              Privacy requests: <a href={`mailto:${PRIVACY_EMAIL}`}>{PRIVACY_EMAIL}</a>
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
