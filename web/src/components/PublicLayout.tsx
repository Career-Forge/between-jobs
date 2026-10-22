import type { ReactNode } from "react";
import { FONT_LICENSES_PATH, PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { LegalLinks } from "./LegalLinks";
import { NextStepSkipLink } from "./NextStepSkipLink";
import { PublicHeader } from "./PublicHeader";
import { MAIN_CONTENT_ID, SkipLink } from "./SkipLink";
import { Wordmark } from "./Wordmark";
import "../styles/public.css";

// What a signed-out visitor sees around every public page (the home page, How it works, Open source and
// the two legal pages): a header (PublicHeader.tsx: the wordmark, the site's tabs and the way in), the
// page itself as the one <main> (the skip link's target), and a footer with the wordmark, the legal
// links, the source and the two contact addresses. No state of its own, so a test can render it directly.
//
// `variant="landing"` is the page that has a next-step band, the home page: its hero holds the page's one
// cyan fill, so the header's Create account is outlined, and a "Skip to next step" link comes ahead of
// "Skip to content". Every other public page has no band (they have no "now"), so its header's Create
// account is the one cyan fill and there is only "Skip to content".

export function PublicLayout({ children, variant = "page" }: { children: ReactNode; variant?: "landing" | "page" }) {
  const landing = variant === "landing";
  return (
    <div className="bj-public">
      {landing && <NextStepSkipLink />}
      <SkipLink />
      <PublicHeader filled={!landing} />
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
