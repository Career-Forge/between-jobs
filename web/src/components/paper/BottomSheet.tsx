import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../icons";
import "../../styles/paper.css";

// A modal sheet pinned to the bottom of a phone screen. Built by hand rather than on <dialog> so the
// behavior the design asks for is the same everywhere and can be tested without a browser:
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

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function tabbablesIn(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((element) => element.getAttribute("aria-hidden") !== "true");
}

export function BottomSheet({ open, onClose, title, returnFocusTo, children }: BottomSheetProps) {
  const sheet = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  // The latest callbacks, so the effect below does not restart (and steal focus back) on a re-render.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const returnRef = useRef(returnFocusTo);
  returnRef.current = returnFocusTo;

  useEffect(() => {
    if (!open) return;
    const root = sheet.current;
    if (root === null) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // Everything else in the document becomes inert; only what this effect changed is restored.
    const madeInert: Element[] = [];
    for (const sibling of document.body.children) {
      if (sibling.contains(root) || sibling.hasAttribute("inert")) continue;
      sibling.setAttribute("inert", "");
      madeInert.push(sibling);
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    closeButton.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const stops = tabbablesIn(root);
      if (stops.length === 0) {
        event.preventDefault();
        root.focus();
        return;
      }
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      if (!root.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (event.target instanceof Node && !root.contains(event.target)) (tabbablesIn(root)[0] ?? root).focus();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("focusin", onFocusIn);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("focusin", onFocusIn);
      for (const element of madeInert) element.removeAttribute("inert");
      document.body.style.overflow = previousOverflow;
      const target = returnRef.current?.current ?? opener;
      if (target !== null && target.isConnected) target.focus();
    };
  }, [open]);

  if (!open) return null;
  return createPortal(
    <div className="bj-sheet-root">
      <div className="bj-sheet-scrim" onClick={() => onCloseRef.current()} />
      <div ref={sheet} className="bj-sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="bj-sheet-head">
          <h2 id={titleId} className="bj-sheet-title">
            {title}
          </h2>
          <button ref={closeButton} type="button" className="bj-quiet bj-sheet-close" aria-label="Close" onClick={() => onCloseRef.current()}>
            <Icon name="close" />
          </button>
        </div>
        <div className="bj-sheet-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
