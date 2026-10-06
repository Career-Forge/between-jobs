import { useEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router-dom";
import { shouldScrollToTop } from "./scrollOnNavigate";

// Scrolls the window to the top when the visitor moves to another page. The decision is
// lib/scrollOnNavigate.ts's; this only feeds it the previous path and acts on the answer.
// Called once, at the top of App, before any early return, so the hook order never changes.
export function useScrollToTopOnNavigate(): void {
  const { pathname, hash } = useLocation();
  const navigationType = useNavigationType();
  const previousPathname = useRef<string | null>(null);

  useEffect(() => {
    if (shouldScrollToTop({ previousPathname: previousPathname.current, pathname, hash, navigationType })) {
      window.scrollTo(0, 0);
    }
    previousPathname.current = pathname;
  }, [pathname, hash, navigationType]);
}
