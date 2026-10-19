import { useEffect, useLayoutEffect, useRef } from "react";

// A page that draws a frame before its data arrives (loading, then the page itself) builds the two
// from different components, so the breadcrumb's link is a NEW element when the page arrives. A
// keyboard that had reached the old one (the first stop in the page, and the only one while it
// loads) would drop to the top of the document, and the person would tab through the whole shell again.
// This hands focus to the new breadcrumb link, but only when it was on the old one and nothing else has
// taken it since.
//
// `frame` names which frame is up; the hook acts when it changes. It is the page's to say, because only
// the page knows which of its frames are separate trees.
export function useKeepBreadcrumbFocus(frame: string): void {
  const onCrumb = useRef(false);
  useEffect(() => {
    const track = (event: FocusEvent) => {
      onCrumb.current = event.target instanceof Element && event.target.closest(".bj-breadcrumb") !== null;
    };
    // A real blur (a click elsewhere, Tab leaving) means the person moved on. Removing the focused element
    // fires the same event, while the element is still attached, so it is looked at again once the
    // removal has finished: a removed element is gone by then, one that was blurred is still there.
    const leave = (event: FocusEvent) => {
      const { target } = event;
      if (!(target instanceof Node)) return;
      queueMicrotask(() => {
        if (target.isConnected) onCrumb.current = false;
      });
    };
    document.addEventListener("focusin", track);
    document.addEventListener("focusout", leave);
    return () => {
      document.removeEventListener("focusin", track);
      document.removeEventListener("focusout", leave);
    };
  }, []);

  const drawn = useRef(frame);
  useLayoutEffect(() => {
    if (drawn.current === frame) return;
    drawn.current = frame;
    if (!onCrumb.current) return;
    const active = document.activeElement;
    if (active !== null && active !== document.body) return;
    document.querySelector<HTMLElement>(".bj-breadcrumb a")?.focus({ preventScroll: true });
  }, [frame]);
}
