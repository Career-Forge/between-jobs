import { Link, useLocation } from "react-router-dom";
import { StaticTrailLine } from "../components/paper/TrailLine";
import { HOW_IT_WORKS, OPEN_SOURCE_SECTION_IDS } from "../content/publicPages";
import { OPEN_SOURCE_PATH } from "../lib/publicRoutes";
import { useScrollToHash } from "../lib/useScrollToHash";
import "../styles/site-pages.css";

// /how-it-works -- the product in four steps, each with one honest sentence on what is plain code and what
// is a model on the person's own key, and one trail line of the kind the real screens draw. The words are
// content/publicPages.ts.
//
// Drawn on the public layout when signed out and inside the shell when signed in (the same way the legal
// pages are), so it carries no account links of its own: the header's Create account is the page's one cyan
// fill. A link such as /how-it-works#prepare scrolls to the step and focuses its heading.

export default function HowItWorks() {
  const { hash } = useLocation();
  useScrollToHash(true, hash);
  const page = HOW_IT_WORKS;
  return (
    <div className="bj-site">
      <div className="bj-site-intro">
        <p className="bj-site-eyebrow">{page.eyebrow}</p>
        <h1>{page.title}</h1>
        <p className="bj-site-lead">{page.lead}</p>
      </div>

      <ol className="bj-how-steps" aria-label={page.stepsLabel}>
        {page.steps.map((step, index) => (
          <li key={step.id} id={step.id} className="bj-how-step">
            <span className="bj-how-number" aria-hidden="true">
              {`0${index + 1}`}
            </span>
            <div className="bj-how-body">
              <h2 tabIndex={-1}>{step.title}</h2>
              <p>{step.text}</p>
              <p className="bj-how-split">{step.split}</p>
              <div className="bj-how-example">
                <p className="bj-site-eyebrow">{step.exampleLabel}</p>
                <StaticTrailLine trail={step.trail} />
              </div>
            </div>
          </li>
        ))}
      </ol>

      <div className="bj-site-closing">
        <p className="bj-site-motto">{page.closing.motto}</p>
        <p>{page.closing.text}</p>
        <p>
          <Link to={{ pathname: OPEN_SOURCE_PATH, hash: `#${OPEN_SOURCE_SECTION_IDS.keys}` }}>{page.closing.keysLinkText}</Link>
        </p>
      </div>
    </div>
  );
}
