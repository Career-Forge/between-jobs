import { useEffect } from "react";

// Writes the page's title into the browser tab. `null` leaves it as it is: the screen that
// asked for null (the sign-in page) sets its own, and the next screen sets the next one, so no
// cleanup is needed. The titles are lib/publicRoutes.ts's.
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    if (title !== null) document.title = title;
  }, [title]);
}
