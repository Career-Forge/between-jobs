import { LANDING } from "../../content/landing";
import { TRAIL_GLYPHS, type TrailGlyphName } from "../../lib/trail";
import { StaticTrailLine } from "../paper/TrailLine";
import "../../styles/landing.css";

// The hero's worked example of the one idea the product is built on: every block that holds fetched,
// computed or generated content ends in a line that says who acted, on what basis and when. The
// sample is a flagged claim from the claim check, drawn the way the workspace draws one. The
// sentence is invented for this page and the card says so; no date is shown because none was read.

const LEGEND: readonly { glyph: TrailGlyphName; words: string }[] = [
  { glyph: "model", words: "written by a model" },
  { glyph: "code", words: "plain code, no model" },
  { glyph: "you", words: "done by you" },
  { glyph: "evidence", words: "waiting for your review" },
];

export function TrailExample() {
  const example = LANDING.trailExample;
  return (
    <div className="bj-trail-example">
      <p className="bj-landing-eyebrow">{example.eyebrow}</p>
      <p className="bj-trail-example-chip">
        <span className="bj-trail-glyph bj-trail-glyph--gold" aria-hidden="true">
          {TRAIL_GLYPHS.evidence.char}
        </span>{" "}
        {example.chip}
      </p>
      <p className="bj-trail-example-claim">
        {"“"}
        {example.claim}
        {"”"}
      </p>
      <p className="bj-trail-example-reason">{example.reason}</p>
      <div className="bj-trail-example-trails">
        <StaticTrailLine trail={{ glyph: "model", actor: "Drafted by your model", basis: "on your key" }} />
        <StaticTrailLine
          trail={{ glyph: "evidence", actor: "Flagged by the claim check", basis: "compared with the achievements picked for this draft" }}
        />
      </div>
      {/* A labelled list, not a labelled paragraph (aria-label is not allowed on a paragraph). The marks are
          decoration, so each item's words say what the mark stands for. role="list" keeps the list a list in
          Safari, which drops the semantics of a list styled with list-style: none. */}
      <ul className="bj-trail-example-legend" role="list" aria-label="What each mark means">
        {LEGEND.map((item) => (
          <li key={item.glyph}>
            <span className={`bj-trail-glyph bj-trail-glyph--${TRAIL_GLYPHS[item.glyph].tone}`} aria-hidden="true">
              {TRAIL_GLYPHS[item.glyph].char}
            </span>{" "}
            {item.words}
          </li>
        ))}
      </ul>
      <p className="bj-trail-example-note">{example.sampleNote}</p>
    </div>
  );
}
