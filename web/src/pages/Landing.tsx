import { Link, useLocation } from "react-router-dom";
import { ProductTour } from "../components/landing/ProductTour";
import { TrailExample } from "../components/landing/TrailExample";
import { NextStepBand } from "../components/paper/NextStepBand";
import { LANDING, LANDING_PROVIDERS, LANDING_PROVIDER_CAVEAT, LANDING_SECTION_IDS } from "../content/landing";
import { TOUR } from "../content/tour";
import { REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { nextStep } from "../lib/nextStep";
import { LOGIN_PATH, OPEN_SOURCE_PATH, PRIVACY_PATH, REGISTER_PATH } from "../lib/publicRoutes";
import { useScrollToHash } from "../lib/useScrollToHash";
import "../styles/landing.css";

// "/" for a signed-out visitor. The words are content/landing.ts; the tour's are content/tour.ts. It
// draws inside PublicLayout, which supplies the header, the one <main> and the footer.
//
// The hero holds the page's next-step band (the landing row: "Create an account, or sign in if you
// have one."), so the page's one cyan fill is the band's button. Everything else that looks like a
// button is outlined, and the tour's depicted buttons are pictures, not controls.
//
// The hero is plain content: no state, no effect, nothing that waits for a script to be worth reading.
// (The tour below it is the page's one stateful piece: it picks which of five drawings shows.)

const REPO_LABEL = REPO_URL.replace(/^https?:\/\//, "");
const OUTCOME = nextStep({ page: "landing", signedIn: false });

export default function Landing() {
  // A link such as /#tour scrolls to the section and focuses its heading. The location key
  // runs it again when the same link is followed twice.
  const { hash, key } = useLocation();
  useScrollToHash(true, hash, key);
  return (
    <div className="bj-landing">
      <section className="bj-landing-hero" aria-labelledby="bj-landing-title">
        <div className="bj-landing-hero-copy">
          <h1 id="bj-landing-title">
            {LANDING.headlineLines.map((line) => (
              <span key={line} className="bj-landing-line">
                {line}{" "}
              </span>
            ))}
          </h1>
          <p className="bj-landing-lead">{LANDING.lead}</p>
          <NextStepBand outcome={OUTCOME} />
          <div className="bj-landing-hero-links">
            <Link to={{ hash: `#${LANDING_SECTION_IDS.tour}` }} className="bj-button-link">
              {LANDING.secondaryCta}
            </Link>
          </div>
          <p className="bj-landing-motto">
            <span className="bj-landing-motto-rule" aria-hidden="true" />
            {LANDING.motto}
          </p>
        </div>
        <div className="bj-landing-hero-art">
          <TrailExample />
        </div>
      </section>

      <section id={LANDING_SECTION_IDS.tour} className="bj-landing-section" aria-labelledby="bj-landing-tour">
        <h2 id="bj-landing-tour" tabIndex={-1}>
          {TOUR.title}
        </h2>
        <ProductTour />
      </section>

      <section id={LANDING_SECTION_IDS.submit} className="bj-landing-section" aria-labelledby="bj-landing-promise">
        <h2 id="bj-landing-promise" tabIndex={-1}>
          {LANDING.promise.title}
        </h2>
        <p className="bj-landing-intro">{LANDING.promise.text}</p>
        <div className="bj-landing-cards">
          <article className="bj-landing-card">
            <h3>{LANDING.gmail.title}</h3>
            <p>
              {LANDING.gmail.text} {LANDING.gmail.policyLead}
              <Link to={PRIVACY_PATH}>{LANDING.gmail.policyLinkText}</Link>
              {LANDING.gmail.policyTail}
            </p>
            <p className="bj-landing-availability">{LANDING.gmail.availability}</p>
          </article>
          <article className="bj-landing-card">
            <h3>{LANDING.extension.title}</h3>
            <p>{LANDING.extension.text}</p>
            <p className="bj-landing-availability">{LANDING.extension.availability}</p>
          </article>
          <article className="bj-landing-card">
            <h3>{LANDING.keys.title}</h3>
            <p>{LANDING.keys.text}</p>
            <p>{LANDING.keys.connect}</p>
            <ul className="bj-landing-providers" aria-label={LANDING.keys.providersLabel}>
              {LANDING_PROVIDERS.map((provider) => (
                <li key={provider}>{provider}</li>
              ))}
            </ul>
            {LANDING_PROVIDER_CAVEAT !== null && <p className="bj-landing-availability">{LANDING_PROVIDER_CAVEAT}</p>}
          </article>
        </div>
      </section>

      <section id={LANDING_SECTION_IDS.source} className="bj-landing-section" aria-labelledby="bj-landing-source">
        <div className="bj-landing-source">
          <div className="bj-landing-source-copy">
            <p className="bj-landing-eyebrow">{LANDING.source.license}</p>
            <h2 id="bj-landing-source" tabIndex={-1}>
              {LANDING.source.title}
            </h2>
            <p>{LANDING.source.text}</p>
          </div>
          <div className="bj-landing-source-link">
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="bj-button-link">
              {LANDING.source.linkText}
              <span className="bj-visually-hidden"> (opens in a new tab)</span>
            </a>
            <span className="bj-landing-repo" aria-hidden="true">
              {REPO_LABEL}
            </span>
            <Link to={OPEN_SOURCE_PATH} className="bj-landing-more">
              {LANDING.source.pageLinkText}
            </Link>
          </div>
        </div>
      </section>

      <section id={LANDING_SECTION_IDS.early} className="bj-landing-section" aria-labelledby="bj-landing-early">
        <h2 id="bj-landing-early" tabIndex={-1}>
          {LANDING.early.title}
        </h2>
        <p className="bj-landing-intro">
          {LANDING.early.lead}
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
        </p>
        <ul className="bj-landing-facts">
          {LANDING.early.facts.map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
        <div className="bj-landing-hero-links">
          <Link to={REGISTER_PATH} className="bj-button-link">
            {LANDING.early.createAccount}
          </Link>
          <Link to={LOGIN_PATH} className="bj-landing-signin">
            {LANDING.early.signIn}
          </Link>
        </div>
      </section>
    </div>
  );
}
