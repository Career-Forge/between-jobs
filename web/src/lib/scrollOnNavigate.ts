// When a route change should put the page back at the top. The app uses a plain BrowserRouter,
// which restores nothing on its own: following a link from the bottom of a long document (the
// Privacy Policy's footer link to the Terms) would open the next page at the same offset, with
// its title thousands of pixels above the viewport. The hook that asks this is
// lib/useScrollToTopOnNavigate.ts; this is the decision, so a test can run it.
//
//   - the first render never scrolls: there is no earlier page, and the browser has already
//     placed a reload where it was;
//   - back and forward ("POP") are the browser's own scroll restoration;
//   - an address with a fragment names its own target (the legal pages' table of contents,
//     the application workspace's cross-links), and scrolling to it is that page's job;
//   - a change of query string or fragment on the same path is not a new page.

export interface ScrollDecisionInput {
  // The path of the page the visitor was on, or null on the first render.
  previousPathname: string | null;
  pathname: string;
  hash: string;
  // "POP", "PUSH" or "REPLACE": what react-router's useNavigationType reports.
  navigationType: string;
}

export function shouldScrollToTop(input: ScrollDecisionInput): boolean {
  if (input.previousPathname === null) return false;
  if (input.navigationType === "POP") return false;
  if (input.hash !== "") return false;
  return input.previousPathname !== input.pathname;
}
