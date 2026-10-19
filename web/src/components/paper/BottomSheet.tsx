import { useId, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useModalDialog } from "../../lib/useModalDialog";
import { Icon } from "../icons";
import "../../styles/paper.css";

// A modal sheet pinned to the bottom of a phone screen. Built by hand rather than on <dialog> so the
// behavior this sheet promises is the same everywhere and can be tested without a browser. The
// keyboard side is useModalDialog's (lib/useModalDialog.ts), the same as the Profile page's entry
// editor:
//
//  - focus moves into the sheet when it opens (to Close) and is trapped there: Tab and Shift+Tab
//    wrap, and focus that lands outside is pulled back;
//  - Esc closes it, and so does pressing the dimmed area or the Close button;
//  - focus returns to what opened it (`returnFocusTo`, because Safari and Firefox on a Mac do not
//    focus a button on click, so "whatever had focus" can be the page body);
//  - the rest of the page is made inert while it is open and the page behind does not scroll.

export interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  /** The sheet's heading, and its accessible name. */
  title: string;
  /** The element focus goes back to on close. Defaults to whatever had focus when the sheet opened. */
  returnFocusTo?: RefObject<HTMLElement | null>;
  children: ReactNode;
}

export function BottomSheet({ open, ...sheet }: BottomSheetProps) {
  // The hook is mounted only while the sheet is open, as useModalDialog asks.
  return open ? <OpenSheet {...sheet} /> : null;
}

function OpenSheet({ onClose, title, returnFocusTo, children }: Omit<BottomSheetProps, "open">) {
  const sheet = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  useModalDialog(sheet, { onClose, initialFocus: () => closeButton.current, returnFocus: () => returnFocusTo?.current ?? null });

  return createPortal(
    <div className="bj-sheet-root">
      <div className="bj-sheet-scrim" onClick={onClose} />
      <div ref={sheet} className="bj-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="bj-sheet-head">
          <h2 id={titleId} className="bj-sheet-title">
            {title}
          </h2>
          <button ref={closeButton} type="button" className="bj-quiet bj-sheet-close" aria-label="Close" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="bj-sheet-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
