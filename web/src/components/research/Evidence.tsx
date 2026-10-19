import type { ReactNode } from "react";
import { safeSourceUrl, type ConfidenceReading } from "../../lib/research";
import { TRAIL_GLYPHS, type TrailGlyphName } from "../../lib/trail";
import "../../styles/paper-doc.css";
import "../../styles/research.css";

// The one way the research sections show evidence: a list of lines, each a source (a link to the page a
// search returned) and, where the model gave one, its confidence as gold cells with the word.
//
//   <EvidenceList label="Sources for this claim">
//     <EvidenceItem url="https://example.com/post" title="Northwind raises a Series B" confidence={intelConfidence("high")} />
//   </EvidenceList>
//
// Company intel claims, a contact's sources and an event's source all use it, and Your play's citations
// use CitationItem, the same line for evidence that is a name from the person's own profile or the role
// rather than a page. The marks are gold because they are evidence (a model's reading, never a checked
// fact): there is no green here, and the word is always beside the cells so color is never the only
// carrier. An address that is not http or https is not a link (it is a stranger's text, or a model's
// copy of one): the title shows as plain words.

export function EvidenceList({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <ul aria-label={label} className={className === undefined ? "bj-rs-evidence" : `bj-rs-evidence ${className}`}>
      {children}
    </ul>
  );
}

// `cells` filled of `of`, then the word. The group reads as "Confidence: High" to assistive tech.
export function ConfidenceMark({ reading, noun = "Confidence" }: { reading: ConfidenceReading; noun?: string }) {
  return (
    <span className="bj-rs-conf">
      <span className="bj-rs-conf-cells" aria-hidden="true">
        {Array.from({ length: reading.of }, (_, index) => (
          <span key={index} className={index < reading.cells ? "bj-segbar-cell bj-segbar-cell--filled" : "bj-segbar-cell"} />
        ))}
      </span>
      <span className="bj-rs-conf-word">
        <span className="bj-visually-hidden">{noun}: </span>
        {reading.word}
      </span>
    </span>
  );
}

// The link to a source. Opens in a new tab, and says so in its name.
export function SourceLink({ url, title }: { url: string; title?: string | null }) {
  const href = safeSourceUrl(url);
  const words = title !== undefined && title !== null && title.trim() !== "" ? title.trim() : url;
  if (href === null) {
    // Not an address a browser should be sent to: the words alone, and nothing that can be activated.
    return <span className="bj-rs-source bj-rs-source--plain">{title !== undefined && title !== null && title.trim() !== "" ? title.trim() : "A source whose address is not shown"}</span>;
  }
  return (
    <a className="bj-rs-source" href={href} target="_blank" rel="noopener noreferrer">
      {words}
      <span className="bj-visually-hidden"> (opens in a new tab)</span>
    </a>
  );
}

export interface EvidenceItemProps {
  url: string;
  title?: string | null;
  confidence?: ConfidenceReading | null;
}

export function EvidenceItem({ url, title, confidence }: EvidenceItemProps) {
  return (
    <li className="bj-rs-ev">
      {confidence !== undefined && confidence !== null && <ConfidenceMark reading={confidence} />}
      <SourceLink url={url} title={title} />
    </li>
  );
}

// Evidence that is not a page: the strength or the gap a brief cites, named as it is in the person's own
// profile or the role's requirements. Gold, with the diamond that means "evidence" in a trail.
export function CitationItem({ children }: { children: ReactNode }) {
  const glyph = TRAIL_GLYPHS.evidence;
  return (
    <li className="bj-rs-ev bj-rs-ev--citation">
      <span className={`bj-trail-glyph bj-trail-glyph--${glyph.tone}`} aria-hidden="true">
        {glyph.char}
      </span>{" "}
      <span className="bj-rs-cite-label">Evidence</span>{" "}
      <span className="bj-rs-cite">{children}</span>
    </li>
  );
}

// An outline chip with a mark and its word: a person's role, an event's certainty. Never a status
// color: solid gold outline is the firmer reading, dashed gold the weaker one, muted the neutral one.
export type ChipTone = "solid" | "dashed" | "muted";

export function Chip({ tone, glyph, children }: { tone: ChipTone; glyph?: TrailGlyphName; children: ReactNode }) {
  const mark = glyph === undefined ? null : TRAIL_GLYPHS[glyph];
  return (
    <span className={`bj-rs-chip bj-rs-chip--${tone}`}>
      {mark !== null && (
        <>
          <span className={`bj-trail-glyph bj-trail-glyph--${mark.tone}`} aria-hidden="true">
            {mark.char}
          </span>{" "}
        </>
      )}
      {children}
    </span>
  );
}

// A gold note that says something the person should know and that is not a failure (the server declined
// to draft because it had nothing to ground a draft in, a lookup that ran and found nothing). It is a
// status region so it is read out when it appears.
export function EvidenceNote({ children, className }: { children: ReactNode; className?: string }) {
  const glyph = TRAIL_GLYPHS.evidence;
  return (
    <p role="status" className={className === undefined ? "bj-rs-note" : `bj-rs-note ${className}`}>
      <span className={`bj-trail-glyph bj-trail-glyph--${glyph.tone}`} aria-hidden="true">
        {glyph.char}
      </span>{" "}
      {children}
    </p>
  );
}
