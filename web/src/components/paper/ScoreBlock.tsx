import type { ReactNode } from "react";
import { GlyphMark } from "./TrailLine";
import "../../styles/paper-doc.css";

// The two score blocks the app draws (the ATS score and the Discover fit), as far as they are generic. Props in, no fetching, no
// scoring: the numbers and the words arrive already decided, and these only lay them out.
//
//   ScoreBlock  The ATS score block: a label with the "engine ✦" mark at the right, the big numeral
//               (gold serif, lining tabular figures) with its muted "/ 100 · medium confidence",
//               then whatever the page puts under it -- usually SegmentedBars -- and a footer line.
//   FitLadder   The Discover fit block's ladder: four gold cells (Poor 1, Mixed 2, Good 3, Strong 4
//               filled) and the words "Good -- 61 / 100".
//
// Both say that code did the math over what a model extracted ("engine ✦"), because that is what the
// trail's own "engine" glyph means; pass `engine={false}` for a number that is not that.

export interface ScoreBlockProps {
  /** "ATS score". */
  label: string;
  /** The score. */
  value: number;
  /** Out of what; 100 by default. */
  max?: number;
  /** An earlier score of the same thing: drawn "64 -> 72" when a regeneration changed it. */
  from?: number | null;
  /** After the "/ 100": "medium confidence". */
  caption?: string;
  /** Draws the "engine ✦" mark at the right of the label. On by default. */
  engine?: boolean;
  /** The bars and callouts under the numeral. */
  children?: ReactNode;
  /** The line under everything: "An honest number, not a verdict." */
  footer?: string;
  className?: string;
}

export function ScoreBlock({ label, value, max = 100, from, caption, engine = true, children, footer, className }: ScoreBlockProps) {
  const changed = typeof from === "number" && Number.isFinite(from) && from !== value;
  return (
    <div className={className === undefined ? "bj-scoreblock" : `bj-scoreblock ${className}`}>
      <div className="bj-scoreblock-head">
        <span className="bj-scoreblock-label">{label}</span>
        {engine && (
          <span className="bj-scoreblock-engine">
            <GlyphMark name="engine" />
          </span>
        )}
      </div>
      <p className="bj-scoreblock-numeral-row">
        <span className="bj-scoreblock-numeral">
          {changed ? (
            <>
              {/* The arrow is drawn for the eye; a screen reader is told the change in words. */}
              <span aria-hidden="true">
                <span className="bj-scoreblock-from">{from} {"->"} </span>
                {value}
              </span>
              <span className="bj-visually-hidden">
                was {from}, now {value}
              </span>
            </>
          ) : (
            value
          )}
        </span>
        <span className="bj-scoreblock-of">
          {" "}
          / {max}
          {caption !== undefined && caption !== "" && ` · ${caption}`}
        </span>
      </p>
      {children !== undefined && children !== null && <div className="bj-scoreblock-body">{children}</div>}
      {footer !== undefined && footer !== "" && <p className="bj-scoreblock-footer">{footer}</p>}
    </div>
  );
}

export interface FitLadderProps {
  /** The rungs, lowest first: ["Poor", "Mixed", "Good", "Strong"]. */
  levels: readonly string[];
  /** The rung reached, by name; null for no rung. */
  level: string | null;
  /** The number beside the name; left out of the words when null or missing. */
  score?: number | null;
  max?: number;
  /** Nothing was scored (a fallback): hollow cells, no number, `placeholderLabel` as the words. */
  placeholder?: boolean;
  placeholderLabel?: string;
  /** Draws the "engine ✦" micro-caption when a rung was reached (never for "Not scored"). On by default. */
  engine?: boolean;
  className?: string;
}

export function FitLadder({ levels, level, score, max = 100, placeholder = false, placeholderLabel = "Placeholder", engine = true, className }: FitLadderProps) {
  const rung = placeholder || level === null ? -1 : levels.indexOf(level);
  // A level the ladder does not have is drawn as no rung and no name rather than guessed at.
  const reached = rung === -1 ? 0 : rung + 1;
  const hasScore = !placeholder && rung !== -1 && typeof score === "number" && Number.isFinite(score);
  const words = placeholder ? placeholderLabel : rung === -1 ? "Not scored" : hasScore ? `${level} -- ${score} / ${max}` : String(level);
  const label = `Fit: ${words}`;
  return (
    <div className={className === undefined ? "bj-ladder" : `bj-ladder ${className}`}>
      <div className="bj-ladder-row" role="img" aria-label={label}>
        <span className="bj-ladder-cells" aria-hidden="true">
          {levels.map((name, index) => (
            <span key={name} className={index < reached ? "bj-ladder-cell bj-ladder-cell--filled" : "bj-ladder-cell"} />
          ))}
        </span>
        <span className="bj-ladder-words" aria-hidden="true">
          {words}
        </span>
      </div>
      {engine && !placeholder && rung !== -1 && (
        <p className="bj-ladder-caption">
          <GlyphMark name="engine" />
        </p>
      )}
    </div>
  );
}
