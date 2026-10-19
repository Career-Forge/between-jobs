import type { ReactNode } from "react";
import { checklistSummary, type ChecklistState } from "../../lib/checklistSummary";
import { TRAIL_GLYPHS, type TrailGlyphName } from "../../lib/trail";
import "../../styles/paper-doc.css";

// The rows of a review checklist -- what was checked, by whom, and what is left for the person.
//
//   <ChecklistGroup title="Checked by Between Jobs" rows={[
//     { id: "one_page", state: "pass", label: "One page" },
//     { id: "no_unsupported_claims", state: "fail", label: "No unsupported claims" },
//   ]} />
//   <ChecklistGroup title="Yours to check" rows={[
//     { id: "links_valid", state: "not_checked", label: "Links work", hint: "Open each link in the PDF." },
//   ]} />
//
// Three states, each a word and a mark as well as a color:
//
//   pass          ✓ Pass          emerald. A check the code ran, and it passed.
//   fail          ✕ Fail          red. A check the code ran, and it failed.
//   not_checked   ○ Not checked   muted, with the hint for doing it yourself.
//
// "Not checked" is never a tick box and never a pass: a row is a statement, not a control, so nothing
// here takes a click, and there is no "reviewed" state to set (nothing stores one). A row is not a
// tab stop; the keyboard reaches only what is put in `actions` (a link, a button), in reading order.
// The caveat is for a check that rests on an assumption ("This check assumes one page; your settings
// allow two.").

export type { ChecklistState };

const MARKS: Record<ChecklistState, { glyph: TrailGlyphName; word: string }> = {
  pass: { glyph: "passed", word: "Pass" },
  fail: { glyph: "failed", word: "Fail" },
  not_checked: { glyph: "code", word: "Not checked" },
};

export interface ChecklistRowProps {
  state: ChecklistState;
  label: string;
  /** For a row that is not checked: how to check it yourself. */
  hint?: string;
  /** What the check assumes, when that matters. */
  caveat?: string;
  /** Links or outlined buttons for this row. */
  actions?: ReactNode;
  as?: "li" | "div";
  className?: string;
}

export function ChecklistRow({ state, label, hint, caveat, actions, as: Tag = "li", className }: ChecklistRowProps) {
  const mark = MARKS[state];
  const glyph = TRAIL_GLYPHS[mark.glyph];
  return (
    <Tag className={className === undefined ? `bj-checkrow bj-checkrow--${state}` : `bj-checkrow bj-checkrow--${state} ${className}`}>
      <span className={`bj-checkrow-state bj-checkrow-state--${state}`}>
        <span className={`bj-trail-glyph bj-trail-glyph--${glyph.tone}`} aria-hidden="true">
          {glyph.char}
        </span>{" "}
        {mark.word}
      </span>
      <div className="bj-checkrow-body">
        <p className="bj-checkrow-label">{label}</p>
        {hint !== undefined && hint !== "" && <p className="bj-checkrow-note">{hint}</p>}
        {caveat !== undefined && caveat !== "" && <p className="bj-checkrow-note">{caveat}</p>}
        {actions !== undefined && actions !== null && <div className="bj-checkrow-actions">{actions}</div>}
      </div>
    </Tag>
  );
}

export interface ChecklistGroupProps {
  /** "Checked by Between Jobs": the count of rows is added, "(6)". */
  title: string;
  rows: readonly (ChecklistRowProps & { id: string })[];
  /** The heading's level, h3 by default (the page's h1 is the PageHeader, a section's chapter is h2). */
  level?: 3 | 4 | 5;
  className?: string;
}

export function ChecklistGroup({ title, rows, level = 3, className }: ChecklistGroupProps) {
  const Heading = `h${level}` as const;
  return (
    <div className={className === undefined ? "bj-checkgroup" : `bj-checkgroup ${className}`}>
      <Heading className="bj-checkgroup-title">
        {title} <span className="bj-checkgroup-count">({rows.length})</span>
      </Heading>
      <ul className="bj-checkgroup-rows" aria-label={title}>
        {rows.map(({ id, ...row }) => (
          <ChecklistRow key={id} {...row} />
        ))}
      </ul>
    </div>
  );
}

// The counts over every row of every group: "5 pass · 1 fail · 4 not checked".
export function ChecklistSummary({ states, className }: { states: readonly ChecklistState[]; className?: string }) {
  return <p className={className === undefined ? "bj-checkgroup-summary" : `bj-checkgroup-summary ${className}`}>{checklistSummary(states)}</p>;
}
