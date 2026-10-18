import { Link } from "react-router-dom";
import { CircuitDemo } from "../components/landing/CircuitDemo";
import { DossierArt } from "../components/landing/DossierArt";
import { TrailExample } from "../components/landing/TrailExample";
import { NextStepBand } from "../components/paper/NextStepBand";
import { LANDING, LANDING_PROVIDERS, LANDING_SECTION_IDS } from "../content/landing";
import { REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { nextStep } from "../lib/nextStep";
import { LOGIN_PATH, PRIVACY_PATH, REGISTER_PATH } from "../lib/publicRoutes";
import { useHashTarget } from "../lib/useHashTarget";
import "../styles/landing.css";

// "/" for a signed-out visitor. The words are content/landing.ts; the circuit is drawn from
// content/landingCircuit.ts. It draws inside PublicLayout, which supplies the header, the one <main>
// and the footer.
//
// The hero holds the page's next-step band (the landing row: "Create an account, or sign in if you
// have one."), so the page's one cyan fill is the band's button. Everything else that looks like a
// button is outlined, and the circuit's depicted buttons are pictures, not controls.
//
// The hero is plain content: no state, no effect, nothing that waits for a script to be worth reading.

const REPO_LABEL = REPO_URL.replace(/^https?:\/\//, "");
const OUTCOME = nextStep({ page: "landing", signedIn: false });

export default function Landing() {
  useHashTarget();
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
            <Link to={{ hash: `#${LANDING_SECTION_IDS.circuit}` }} className="bj-button-link">
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

      <section id={LANDING_SECTION_IDS.circuit} className="bj-landing-section" aria-labelledby="bj-landing-circuit">
        <h2 id="bj-landing-circuit" tabIndex={-1}>
          {LANDING.circuit.title}
        </h2>
        <CircuitDemo />
      </section>

      <section id={LANDING_SECTION_IDS.how} className="bj-landing-section" aria-labelledby="bj-landing-how">
        <div className="bj-landing-dossier">
          <div className="bj-landing-dossier-copy">
            <p className="bj-landing-eyebrow">{LANDING.dossier.eyebrow}</p>
            <h2 id="bj-landing-how" tabIndex={-1}>
              {LANDING.dossier.title}
            </h2>
            <p>{LANDING.dossier.text}</p>
          </div>
          <DossierArt />
        </div>
        <ul className="bj-landing-chapters">
          {LANDING.chapters.map((chapter, index) => (
            <li key={chapter.title} className="bj-landing-chapter">
              <span className="bj-landing-chapter-number" aria-hidden="true">
                {index + 1}
              </span>
              <div>
                <h3>{chapter.title}</h3>
                <p>{chapter.text}</p>
              </div>
            </li>
          ))}
        </ul>
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
