import { useEffect, useRef, type RefObject } from "react";

// What a modal dialog owes the keyboard (the entry editor on the Profile page and the bottom sheet
// that holds a receipt use it):
//
//  - focus moves into the dialog when it opens, to `initialFocus` or else the first control in it, and
//    is trapped there: Tab and Shift+Tab wrap, and focus that lands outside is pulled back;
//  - Esc closes it (unless `canClose` is false, as while a save is out);
//  - focus returns to what opened it when it closes (or to `returnFocus`, for a page that knows better:
//    Safari and Firefox on a Mac do not focus a button on click, so "whatever had focus" can be the page
//    body, and the opener may be gone by then);
//  - the rest of the page is inert while it is open, and the page behind does not scroll.
//
// Call it with a ref to the dialog's element, from a component that is mounted only while the dialog
// is open, and draw the dialog through a portal into <body> so every other child of <body> can be made
// inert.

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function tabbablesIn(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (element) => element.getAttribute("aria-hidden") !== "true" && element.getAttribute("aria-disabled") !== "true",
  );
}

export interface ModalDialogOptions {
  onClose: () => void;
  /** Whether Esc may close it right now. */
  canClose?: boolean;
  /** The control that takes focus first. Defaults to the first control in the dialog. */
  initialFocus?: () => HTMLElement | null;
  /** Where focus goes on close, asked for when it closes. Defaults to whatever had focus on open. */
  returnFocus?: () => HTMLElement | null;
}

export function useModalDialog(root: RefObject<HTMLElement | null>, { onClose, canClose = true, initialFocus, returnFocus }: ModalDialogOptions): void {
  // The latest values, so the effect below runs once per opening and a re-render never steals focus back.
  const latest = useRef({ onClose, canClose, initialFocus, returnFocus });
  latest.current = { onClose, canClose, initialFocus, returnFocus };

  useEffect(() => {
    const dialog = root.current;
    if (dialog === null) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const madeInert: Element[] = [];
    for (const sibling of document.body.children) {
      if (sibling.contains(dialog) || sibling.hasAttribute("inert")) continue;
      sibling.setAttribute("inert", "");
      madeInert.push(sibling);
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    (latest.current.initialFocus?.() ?? tabbablesIn(dialog)[0] ?? dialog).focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!latest.current.canClose) return;
        event.preventDefault();
        latest.current.onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const stops = tabbablesIn(dialog);
      if (stops.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      if (!dialog.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && (active === first || active === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (event.target instanceof Node && !dialog.contains(event.target)) (tabbablesIn(dialog)[0] ?? dialog).focus();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("focusin", onFocusIn);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("focusin", onFocusIn);
      for (const element of madeInert) element.removeAttribute("inert");
      document.body.style.overflow = previousOverflow;
      const target = latest.current.returnFocus?.() ?? opener;
      if (target !== null && target.isConnected) target.focus();
    };
    // `root` is a ref: its identity never changes, and the dialog is mounted once.
  }, [root]);
}
