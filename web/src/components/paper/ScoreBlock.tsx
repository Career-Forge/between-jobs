import type { ReactNode } from "react";
import { GRADE_LABEL, GRADE_LADDER, isFitGrade, type FitGrade } from "../../lib/fitGrade";
import { isScoreWithin } from "../../lib/scoreRange";
import { GlyphMark } from "./TrailLine";
import "../../styles/paper-doc.css";

// The two score blocks the app draws (the ATS score and the Discover fit), as far as they are generic. Props in, no fetching, no
// scoring: the numbers and the words arrive already decided, and these only lay them out.
//
//   ScoreBlock  The ATS score block: a label with the "engine ✦" mark at the right, the big numeral
//               (gold serif, lining tabular figures) with its muted "/ 100 · medium confidence",
//               then whatever the page puts under it -- usually SegmentedBars -- and a footer line.
//   FitLadder   The Discover fit block's ladder: three cells, one per grade a person is shown (Bad fit 1,
//               Decent 2, Good 3 filled), and the words "Decent -- 61 / 100". The filled cells take the
//               grade's own colour (rose, yellow, green), never gold, and the word is always beside them:
//               the colour repeats the word and never stands in for it. The grades, and which of the
//               engine's bins each one is shown for, are lib/fitGrade.ts.
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
  /** The grade reached, which is the rung; null (or anything that is not a grade) for no rung. */
  grade: FitGrade | null;
  /** The number beside the name; left out of the words when null, missing or outside 0 to `max`. */
  score?: number | null;
  max?: number;
  /** Nothing was scored (a fallback): hollow cells, no number, `placeholderLabel` as the words. */
  placeholder?: boolean;
  placeholderLabel?: string;
  /** Draws the "engine ✦" micro-caption when a rung was reached (never for "Not scored"). On by default. */
  engine?: boolean;
  /**
   * Draws the words beside the cells ("Decent -- 61 / 100", or "Placeholder"). On by default. A caller that
   * prints the number and the grade itself leaves them off; the image's label still says all of it.
   */
  showWords?: boolean;
  className?: string;
}

export function FitLadder({ grade, score, max = 100, placeholder = false, placeholderLabel = "Placeholder", engine = true, showWords = true, className }: FitLadderProps) {
  // A value that is not one of the grades is drawn as no rung and no name rather than guessed at.
  const reached = placeholder || !isFitGrade(grade) ? null : grade;
  const filled = reached === null ? 0 : GRADE_LADDER.indexOf(reached) + 1;
  const hasScore = reached !== null && isScoreWithin(score, max);
  const words = placeholder ? placeholderLabel : reached === null ? "Not scored" : hasScore ? `${GRADE_LABEL[reached]} -- ${score} / ${max}` : GRADE_LABEL[reached];
  const label = `Fit: ${words}`;
  // The grade's class is what colours the filled cells, from a fixed list of three.
  const classes = ["bj-ladder", reached === null ? null : `bj-ladder--${reached}`, className ?? null].filter((name) => name !== null).join(" ");
  return (
    <div className={classes}>
      <div className="bj-ladder-row" role="img" aria-label={label}>
        <span className="bj-ladder-cells" aria-hidden="true">
          {GRADE_LADDER.map((rung, index) => (
            <span key={rung} className={index < filled ? "bj-ladder-cell bj-ladder-cell--filled" : "bj-ladder-cell"} />
          ))}
        </span>
        {showWords && (
          <span className="bj-ladder-words" aria-hidden="true">
            {words}
          </span>
        )}
      </div>
      {engine && reached !== null && (
        <p className="bj-ladder-caption">
          <GlyphMark name="engine" />
        </p>
      )}
    </div>
  );
}
