import { useLocation } from "react-router-dom";
import { WithCode } from "../components/WithCode";
import { LANDING_PROVIDERS, LANDING_PROVIDER_CAVEAT } from "../content/landing";
import { OPEN_SOURCE, OPEN_SOURCE_SECTION_IDS } from "../content/publicPages";
import { REPO_URL } from "../content/site";
import { useScrollToHash } from "../lib/useScrollToHash";
import "../styles/site-pages.css";

// /open-source -- what the repository holds, how bring-your-own-key works and how to run it yourself. The
// words are content/publicPages.ts, taken from the repository's README. The provider list and its caveat
// are the home page's, built from the same table (content/landing.ts), so the two cannot disagree.
//
// Drawn on the public layout when signed out and inside the shell when signed in, so it carries no account
// links: the header's Create account is the page's one cyan fill, and the GitHub link here is outlined.

const REPO_LABEL = REPO_URL.replace(/^https?:\/\//, "");

export default function OpenSource() {
  const { hash } = useLocation();
  useScrollToHash(true, hash);
  const page = OPEN_SOURCE;
  return (
    <div className="bj-site">
      <div className="bj-site-intro">
        <p className="bj-site-eyebrow">{page.eyebrow}</p>
        <h1>{page.title}</h1>
        <p className="bj-site-lead">{page.lead}</p>
        <div className="bj-site-repo">
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="bj-button-link">
            {page.linkText}
            <span className="bj-visually-hidden"> (opens in a new tab)</span>
          </a>
          <span className="bj-site-repo-label" aria-hidden="true">
            {REPO_LABEL}
          </span>
        </div>
      </div>

      <section id={OPEN_SOURCE_SECTION_IDS.repository} className="bj-site-section" aria-labelledby="bj-os-repository">
        <h2 id="bj-os-repository" tabIndex={-1}>
          {page.repository.title}
        </h2>
        <dl className="bj-site-list">
          {page.repository.items.map((item) => (
            <div key={item.name} className="bj-site-list-row">
              <dt>{item.name}</dt>
              <dd>{item.text}</dd>
            </div>
          ))}
        </dl>
        <p className="bj-site-note">{page.repository.notIncluded}</p>
      </section>

      <section id={OPEN_SOURCE_SECTION_IDS.keys} className="bj-site-section" aria-labelledby="bj-os-keys">
        <h2 id="bj-os-keys" tabIndex={-1}>
          {page.keys.title}
        </h2>
        <p>{page.keys.text}</p>
        <p>{page.keys.providersLead}</p>
        <ul className="bj-site-chips" aria-label={page.keys.providersLabel}>
          {LANDING_PROVIDERS.map((provider) => (
            <li key={provider}>{provider}</li>
          ))}
        </ul>
        <p className="bj-site-note">{page.keys.oneClick}</p>
        {LANDING_PROVIDER_CAVEAT !== null && <p className="bj-site-note">{LANDING_PROVIDER_CAVEAT}</p>}
      </section>

      <section id={OPEN_SOURCE_SECTION_IDS.selfHost} className="bj-site-section" aria-labelledby="bj-os-self-host">
        <h2 id="bj-os-self-host" tabIndex={-1}>
          {page.selfHost.title}
        </h2>
        {page.selfHost.paragraphs.map((paragraph) => (
          <p key={paragraph}>
            <WithCode text={paragraph} />
          </p>
        ))}
      </section>
    </div>
  );
}
