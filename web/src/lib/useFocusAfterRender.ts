import { useCallback, useEffect, useRef, type RefObject } from "react";

// Keeps a keyboard's place when an action takes the control that had focus away or moves it: pressing
// Remove on a row removes the Remove button, and a row that moves in the DOM can lose focus in a browser.
// The caller says which control should have focus next, by the `data-focus-key` it carries inside
// `root`, in the same handler that changes the state; after that change has rendered, focus goes there.
//
// Ask only for an action that really changes something: a request is consumed by the next render, so one
// made for a press that changed nothing would take focus at some later, unrelated render.
export function useFocusAfterRender(root: RefObject<HTMLElement | null>): (key: string) => void {
  const wanted = useRef<string | null>(null);
  useEffect(() => {
    const key = wanted.current;
    if (key === null) return;
    wanted.current = null;
    for (const candidate of root.current?.querySelectorAll<HTMLElement>("[data-focus-key]") ?? []) {
      if (candidate.dataset.focusKey === key) {
        candidate.focus();
        return;
      }
    }
  });
  return useCallback((key: string) => {
    wanted.current = key;
  }, []);
}
