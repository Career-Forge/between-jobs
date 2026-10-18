import { Fragment, useId, useRef, useState } from "react";
import {
  TRAIL_GLYPHS,
  buildReceipt,
  buildTrail,
  type ReceiptModel,
  type ReceiptRowInput,
  type TrailGlyphName,
  type TrailInput,
  type TrailModel,
  type TrailPart,
} from "../../lib/trail";
import { useIsPhone } from "../../lib/useMediaQuery";
import { BottomSheet } from "./BottomSheet";
import "../../styles/paper.css";

// The trail line under a block: who acted, on what basis, and when (lib/trail.ts is the grammar).
//
//   <TrailLine trail={{ glyph: "model", actor: "Drafted by your model", basis: "on your key" }}
//              receipt={[{ label: "run", value: runId }, ...]} />
//
// With a receipt it is a real <button> (44px tall around 12px text) that opens the receipt: inline
// under the line, or on a phone (under 960px) as a focus-trapped bottom sheet. Without one -- or when
// every receipt row was omitted for want of a returned field -- it is the same line as plain text, so
// a reader is never offered something to open that holds nothing. A line with no words left renders
// nothing.
//
// The parts are exported for the right-margin layout of document pages: `TrailText` draws a line's
// words and glyphs, `ReceiptWell` the receipt, and `open` / `onOpenChange` let the margin keep one
// receipt open at a time.

export function GlyphMark({ name }: { name: TrailGlyphName }) {
  const glyph = TRAIL_GLYPHS[name];
  const mark = (
    <span className={`bj-trail-glyph bj-trail-glyph--${glyph.tone}`} aria-hidden="true">
      {glyph.char}
    </span>
  );
  if (glyph.word === undefined) return mark;
  // "engine ✦": the word is part of what is read out, the star is not.
  return (
    <>
      <span className="bj-trail-engine">{glyph.word}</span> {mark}
    </>
  );
}

function PartView({ part }: { part: TrailPart }) {
  if (typeof part === "string") return <>{part}</>;
  if ("glyph" in part) return <GlyphMark name={part.glyph} />;
  return <span className="bj-trail-mono">{part.mono}</span>;
}

// The line's words and glyphs, separated by " · ".
export function TrailText({ model }: { model: TrailModel }) {
  return (
    <>
      {model.segments.map((segment, index) => (
        <Fragment key={index}>
          {index > 0 && <span className="bj-trail-sep"> {"·"} </span>}
          {segment.map((part, partIndex) => (
            <Fragment key={partIndex}>
              {partIndex > 0 && " "}
              <PartView part={part} />
            </Fragment>
          ))}
        </Fragment>
      ))}
    </>
  );
}

// A definition list may hold only term/description groups, so a note (a sentence with no label)
// cannot sit inside one. The rows are cut into runs in their original order: each run of labelled
// rows is its own <dl>, and each note is a paragraph between them. A receipt of only notes has no
// <dl> at all.
function runsOf(rows: ReceiptModel["rows"]) {
  const runs: ({ notes: string } | { rows: { label: string; value: string }[] })[] = [];
  for (const row of rows) {
    if ("note" in row) {
      runs.push({ notes: row.note });
      continue;
    }
    const last = runs[runs.length - 1];
    if (last !== undefined && "rows" in last) last.rows.push(row);
    else runs.push({ rows: [row] });
  }
  return runs;
}

export function ReceiptWell({ receipt, title }: { receipt: ReceiptModel; title: string }) {
  return (
    <div className="bj-receipt" role="group" aria-label={title} tabIndex={0}>
      {runsOf(receipt.rows).map((run, index) =>
        "notes" in run ? (
          <p key={index} className="bj-receipt-note">
            {run.notes}
          </p>
        ) : (
          <dl key={index}>
            {run.rows.map((row, rowIndex) => (
              <div key={rowIndex} className="bj-receipt-row">
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        ),
      )}
    </div>
  );
}

export interface TrailLineProps {
  trail: TrailInput;
  /** What opens under the line. Omit it, or let every row be omitted, for a plain line. */
  receipt?: readonly ReceiptRowInput[] | null;
  /** The receipt's heading (the bottom sheet's title, the inline well's name). */
  receiptTitle?: string;
  /** "auto" is inline from 960px up and a sheet under it. */
  placement?: "auto" | "inline" | "sheet";
  /** Controlled open state; leave both out and the line keeps its own. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

export function StaticTrailLine({ trail, className }: { trail: TrailInput; className?: string }) {
  const model = buildTrail(trail);
  if (model === null) return null;
  return (
    <p className={className === undefined ? "bj-trail bj-trail--static" : `bj-trail bj-trail--static ${className}`}>
      <TrailText model={model} />
    </p>
  );
}

export function TrailLine({ trail, receipt, className, ...rest }: TrailLineProps) {
  const model = buildTrail(trail);
  const receiptModel = receipt === null || receipt === undefined ? null : buildReceipt(receipt);
  if (model === null) return null;
  if (receiptModel === null) return <StaticTrailLine trail={trail} className={className} />;
  return <OpenableTrailLine model={model} receipt={receiptModel} className={className} {...rest} />;
}

function OpenableTrailLine({
  model,
  receipt,
  receiptTitle = "Where this came from",
  placement = "auto",
  open,
  onOpenChange,
  className,
}: { model: TrailModel; receipt: ReceiptModel; className?: string } & Omit<TrailLineProps, "trail" | "receipt" | "className">) {
  const [ownOpen, setOwnOpen] = useState(false);
  const isOpen = open ?? ownOpen;
  const phone = useIsPhone();
  const asSheet = placement === "sheet" || (placement === "auto" && phone);
  const trigger = useRef<HTMLButtonElement>(null);
  const receiptId = useId();

  const setOpen = (next: boolean) => {
    if (open === undefined) setOwnOpen(next);
    onOpenChange?.(next);
  };

  return (
    <div className={className === undefined ? "bj-trail-block" : `bj-trail-block ${className}`}>
      <button
        ref={trigger}
        type="button"
        className="bj-trail bj-trail-trigger"
        aria-expanded={isOpen}
        aria-controls={isOpen && !asSheet ? receiptId : undefined}
        aria-haspopup={asSheet ? "dialog" : undefined}
        onClick={() => setOpen(!isOpen)}
      >
        <span className="bj-trail-words">
          <TrailText model={model} />
        </span>
      </button>
      {isOpen && !asSheet && (
        <div id={receiptId} className="bj-trail-receipt">
          <ReceiptWell receipt={receipt} title={receiptTitle} />
        </div>
      )}
      {asSheet && (
        <BottomSheet open={isOpen} onClose={() => setOpen(false)} title={receiptTitle} returnFocusTo={trigger}>
          <ReceiptWell receipt={receipt} title={receiptTitle} />
        </BottomSheet>
      )}
    </div>
  );
}
