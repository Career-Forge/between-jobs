import { Link } from "react-router-dom";
import { LANDING } from "../content/landing";
import { REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { LOGIN_PATH, PRIVACY_PATH, REGISTER_PATH } from "../lib/publicRoutes";

// "/" for a signed-out visitor. Static: what the product is, the promise that you always press
// submit, bring-your-own-keys, and the way in. The words are content/landing.ts. It draws inside
// PublicLayout, which supplies the header, the one <main> and the footer.
export default function Landing() {
  return (
    <div className="bj-landing">
      <section className="bj-landing-hero" aria-labelledby="bj-landing-title">
        <h1 id="bj-landing-title">{LANDING.headline}</h1>
        <p className="bj-landing-lead">{LANDING.lead}</p>
        <div className="bj-actions">
          <Link to={REGISTER_PATH} className="bj-button-link bj-button-link-primary">
            Create account
          </Link>
          <Link to={LOGIN_PATH} className="bj-button-link">
            Sign in
          </Link>
        </div>
      </section>

      <section aria-labelledby="bj-landing-does">
        <h2 id="bj-landing-does">What it does</h2>
        <ul className="bj-landing-grid">
          {LANDING.features.map((feature) => (
            <li key={feature.title} className="bj-landing-item">
              <h3>{feature.title}</h3>
              <p>{feature.text}</p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="bj-landing-promise">
        <h2 id="bj-landing-promise">{LANDING.promise.title}</h2>
        <p>{LANDING.promise.text}</p>
        <p>
          {LANDING.gmail.text} {LANDING.gmail.policyLead}
          <Link to={PRIVACY_PATH}>{LANDING.gmail.policyLinkText}</Link>
          {LANDING.gmail.policyTail}
        </p>
      </section>

      <section aria-labelledby="bj-landing-keys">
        <h2 id="bj-landing-keys">{LANDING.keys.title}</h2>
        <p>{LANDING.keys.text}</p>
      </section>

      <section aria-labelledby="bj-landing-source">
        <h2 id="bj-landing-source">{LANDING.source.title}</h2>
        <p>{LANDING.source.text}</p>
        <p>
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
            {LANDING.source.linkText}
            <span className="bj-visually-hidden"> (opens in a new tab)</span>
          </a>
        </p>
      </section>

      <section aria-labelledby="bj-landing-early">
        <h2 id="bj-landing-early">{LANDING.early.title}</h2>
        <p>
          {LANDING.early.lead}
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
        </p>
      </section>
    </div>
  );
}
