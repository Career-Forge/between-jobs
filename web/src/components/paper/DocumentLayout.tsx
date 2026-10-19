import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useIsPhone, useMediaQuery } from "../../lib/useMediaQuery";
import { TrailLine, type TrailLineProps } from "./TrailLine";
import "../../styles/paper.css";
import "../../styles/paper-doc.css";

// The page layout for document-like screens (the application workspace and Profile): a column of
// blocks, each with its trail line. Where that trail goes depends on the width, and the page does not
// have to know:
//
//   1280px and up   a 280px right margin with a 1px maroon rule. A block's trail line is a button in
//                   the margin beside the block; opening it shows the receipt in the margin.
//   960 - 1279px    no margin. Each trail line sits under its block and its receipt opens inline, as
//                   a disclosure under the line. The page's "current state" summary (if it gave one)
//                   is shown once, above the first block, in place of what the margin held.
//   under 960px     the trail line sits under its block and its receipt is the focus-trapped
//                   bottom sheet (BottomSheet).
//
// The page writes the same thing at every width:
//
//   <DocumentLayout summary={<p>Status saved · resume drafted</p>}>
//     <SectionChapter id="ch-draft" title="Draft" />
//     <DocumentBlock>
//       <ResumeStructure />      // renders <BlockTrail trail={...} receipt={...} /> wherever it likes
//     </DocumentBlock>
//     <DocumentBlock trail={{ glyph: "code", actor: "Checked by code" }} receipt={rows}>
//       ...
//     </DocumentBlock>
//   </DocumentLayout>
//
// Reading order never changes: a block's DOM is the block, then its margin cell (its trail line), then
// the next block. So at every width the keyboard meets block, trail, block, trail, and the margin
// never forms a tab run of its own. Only one receipt is open at a time in the whole layout.

// From here up the margin exists (the same width Today's right margin starts at). Under PHONE_QUERY
// (useMediaQuery.ts) receipts are sheets. The stylesheet does not repeat these widths: it styles the
// mode classes this module puts on the layout, so the two can never disagree.
export const DOCUMENT_MARGIN_QUERY = "(min-width: 1280px)";

export type DocumentMode = "margin" | "inline" | "sheet";

// Which of the three placements applies at the current width. Usable anywhere (the jump bar reads
// it too); it needs no layout around it.
export function useDocumentMode(): DocumentMode {
  const wide = useMediaQuery(DOCUMENT_MARGIN_QUERY);
  const phone = useIsPhone();
  if (phone) return "sheet";
  return wide ? "margin" : "inline";
}

interface DocumentState {
  mode: DocumentMode;
  openId: string | null;
  setOpen: (id: string, open: boolean) => void;
}

interface BlockState {
  /** The block's margin cell, once it is on the page (only at 1280px and up). */
  marginCell: HTMLElement | null;
  setMarginCell: (element: HTMLElement | null) => void;
}

const DocumentContext = createContext<DocumentState | null>(null);
const BlockContext = createContext<BlockState | null>(null);

export type DocumentMeasure = "prose" | "wide" | "full";

export interface DocumentLayoutProps {
  children: ReactNode;
  /**
   * The compact "current state" the margin's contents collapse into at 960-1279px: a short line or
   * two the page writes from what it already knows. Not drawn at any other width (the margin is the
   * state there, or on a phone the blocks' own trails are).
   */
  summary?: ReactNode;
  /** The summary's accessible name. */
  summaryLabel?: string;
  /**
   * How wide the document column is: "prose" 680px, "wide" 880px (the default, for blocks that hold
   * rows and cards), "full" the whole content width.
   */
  measure?: DocumentMeasure;
  className?: string;
}

export function DocumentLayout({ children, summary, summaryLabel = "Current state", measure = "wide", className }: DocumentLayoutProps) {
  const mode = useDocumentMode();
  const [openId, setOpenId] = useState<string | null>(null);

  // A receipt does not follow a resize: it would reappear as a sheet that takes focus, or in a place
  // the person was not looking at.
  useEffect(() => setOpenId(null), [mode]);

  const setOpen = useCallback((id: string, open: boolean) => {
    setOpenId((current) => (open ? id : current === id ? null : current));
  }, []);
  const value = useMemo(() => ({ mode, openId, setOpen }), [mode, openId, setOpen]);

  const hasSummary = summary !== undefined && summary !== null && summary !== false;
  const classes = ["bj-doc", `bj-doc--${mode}`, `bj-doc--${measure}`];
  if (className !== undefined) classes.push(className);

  return (
    <DocumentContext.Provider value={value}>
      <div className={classes.join(" ")} data-document-mode={mode}>
        {mode === "inline" && hasSummary && (
          <div role="group" aria-label={summaryLabel} className="bj-doc-summary">
            {summary}
          </div>
        )}
        {children}
      </div>
    </DocumentContext.Provider>
  );
}

// The margin beside one block: where its trail line is drawn from 1280px. DocumentBlock puts one
// after its content; it is exported for a page that lays a block out by hand.
export function TrailMargin() {
  const block = useContext(BlockContext);
  return <div className="bj-doc-margin" ref={block?.setMarginCell} />;
}

export interface BlockTrailProps {
  trail: TrailLineProps["trail"];
  /** What opens when the line is pressed. Without one the line is plain words, as in TrailLine. */
  receipt?: TrailLineProps["receipt"];
  /** The receipt's heading (the sheet's title, the receipt well's name). */
  receiptTitle?: string;
  className?: string;
}

// A block's trail line, placed for the current width: in the block's margin cell at 1280px and up,
// otherwise right where this is written, with its receipt inline or as a sheet. Write it once, at the
// end of the block. Outside a DocumentLayout it is a plain TrailLine.
export function BlockTrail({ trail, receipt, receiptTitle, className }: BlockTrailProps) {
  const layout = useContext(DocumentContext);
  const block = useContext(BlockContext);
  const id = useId();
  const open = layout !== null && layout.openId === id;
  const setOpen = layout?.setOpen;

  // A block that goes away with its receipt open must not leave the layout thinking one is open.
  useEffect(
    () => () => {
      setOpen?.(id, false);
    },
    [id, setOpen],
  );

  if (layout === null) return <TrailLine trail={trail} receipt={receipt} receiptTitle={receiptTitle} className={className} />;

  const inMargin = layout.mode === "margin" && block !== null;
  const asSheet = layout.mode === "sheet";

  // Esc closes an open inline or margin receipt and puts focus back on the line, so keyboard focus is
  // never left on a well that has just gone. (The sheet closes itself.)
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    setOpen?.(id, false);
    event.currentTarget.querySelector<HTMLElement>("button.bj-trail-trigger")?.focus();
  };

  const line = (
    <div className="bj-doc-trail" onKeyDown={asSheet ? undefined : onKeyDown}>
      <TrailLine
        trail={trail}
        receipt={receipt}
        receiptTitle={receiptTitle}
        className={className}
        placement={asSheet ? "sheet" : "inline"}
        open={open}
        onOpenChange={(next) => layout.setOpen(id, next)}
      />
    </div>
  );

  if (!inMargin) return line;
  // The cell is a sibling that has just been mounted: until the layout has it, there is nowhere to draw.
  return block.marginCell === null ? null : createPortal(line, block.marginCell);
}

export interface DocumentBlockProps {
  children?: ReactNode;
  /** The block's own anchor, for a block that is a jump target. */
  id?: string;
  /** A shorthand for <BlockTrail> as the last thing in the block. */
  trail?: BlockTrailProps["trail"];
  receipt?: BlockTrailProps["receipt"];
  receiptTitle?: string;
  className?: string;
}

// One block of the document and, from 1280px, the margin cell for its trail line. Outside a
// DocumentLayout it is just a wrapper.
export function DocumentBlock({ children, id, trail, receipt, receiptTitle, className }: DocumentBlockProps) {
  const layout = useContext(DocumentContext);
  const [marginCell, setMarginCell] = useState<HTMLElement | null>(null);
  const value = useMemo(() => ({ marginCell, setMarginCell }), [marginCell]);
  return (
    <BlockContext.Provider value={value}>
      <div id={id} className={className === undefined ? "bj-doc-block" : `bj-doc-block ${className}`}>
        <div className="bj-doc-main">
          {children}
          {trail !== undefined && <BlockTrail trail={trail} receipt={receipt} receiptTitle={receiptTitle} />}
        </div>
        {layout?.mode === "margin" && <TrailMargin />}
      </div>
    </BlockContext.Provider>
  );
}
