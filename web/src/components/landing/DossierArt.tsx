import { LANDING } from "../../content/landing";
import { StaticTrailLine } from "../paper/TrailLine";
import "../../styles/landing.css";

// A drawing of one role's dossier laid over a printed resume, made of boxes and bars: no image, no
// photograph. The whole picture is decoration (aria-hidden); the paragraph beside it says the same
// in words, and a visible note under it says the contents are a sample. The resume page is white
// because it stands for a printed page, so its colors are its own (landing.css).

function Bars({ widths }: { widths: readonly string[] }) {
  return (
    <span className="bj-dossier-bars">
      {widths.map((width, index) => (
        <span key={index} className={`bj-dossier-bar bj-dossier-bar--${width}`} />
      ))}
    </span>
  );
}

export function DossierArt() {
  return (
    <figure className="bj-dossier-figure">
      <div className="bj-dossier-art" aria-hidden="true">
        <div className="bj-resume-sheet">
          <p className="bj-resume-name">Your Name</p>
          <p className="bj-resume-contact">you@email.com · City, State</p>
          <p className="bj-resume-heading">Summary</p>
          <Bars widths={["full", "wide", "mid"]} />
          <p className="bj-resume-heading">Experience</p>
          <Bars widths={["role", "full", "wide", "mid", "role", "full", "mid"]} />
          <p className="bj-resume-heading">Education</p>
          <Bars widths={["mid", "short"]} />
          <p className="bj-resume-heading">Skills</p>
          <Bars widths={["wide", "mid"]} />
        </div>
        <div className="bj-dossier-card">
          <div className="bj-dossier-card-top">
            <span className="bj-dossier-chip bj-dossier-chip--sample">{LANDING.dossier.sampleChip}</span>
            <span className="bj-dossier-chip">
              <span aria-hidden="true">{"◇"}</span> Saved
            </span>
          </div>
          <p className="bj-dossier-role">{LANDING.circuit.sampleRole}</p>
          <p className="bj-dossier-company">{LANDING.circuit.sampleCompany}</p>
          <p className="bj-dossier-tabs">
            <span>Resume</span>
            <span>Research</span>
            <span>Prepare</span>
          </p>
          <p className="bj-dossier-label">About this role</p>
          <Bars widths={["full", "wide", "mid"]} />
          <p className="bj-dossier-label">Your evidence</p>
          <div className="bj-dossier-row">
            <span className="bj-dossier-dot" />
            <span>
              Profile
              <span className="bj-dossier-row-sub">Your experience, skills, background</span>
            </span>
          </div>
          <div className="bj-dossier-row">
            <span className="bj-dossier-dot" />
            <span>
              Posting
              <span className="bj-dossier-row-sub">The role, as it was listed</span>
            </span>
          </div>
          <StaticTrailLine trail={{ glyph: "code", actor: "Assembled by code", basis: "from your saved items" }} />
        </div>
      </div>
      <figcaption className="bj-dossier-caption">{LANDING.dossier.sampleNote}</figcaption>
    </figure>
  );
}
