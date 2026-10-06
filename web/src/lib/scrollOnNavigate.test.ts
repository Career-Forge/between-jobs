import { describe, expect, it } from "vitest";
import { shouldScrollToTop, type ScrollDecisionInput } from "./scrollOnNavigate";

// The rule for "this route change starts at the top of the new page", run rather than read.
// lib/useScrollToTopOnNavigate.ts feeds it the real location; publicPagesWiring.test.ts pins
// that App calls that hook above its first early return.

function decide(overrides: Partial<ScrollDecisionInput>): boolean {
  return shouldScrollToTop({
    previousPathname: "/privacy",
    pathname: "/terms",
    hash: "",
    navigationType: "PUSH",
    ...overrides,
  });
}

describe("shouldScrollToTop", () => {
  it("scrolls to the top when a link takes the visitor to another page (the footer's Privacy -> Terms)", () => {
    expect(decide({})).toBe(true);
    expect(decide({ previousPathname: "/", pathname: "/privacy" })).toBe(true);
  });

  it("scrolls on a replace too: a redirect lands on a new page", () => {
    expect(decide({ navigationType: "REPLACE" })).toBe(true);
  });

  it("does not scroll on the first render: there is no earlier page, and a reload keeps its place", () => {
    expect(decide({ previousPathname: null, navigationType: "POP" })).toBe(false);
    expect(decide({ previousPathname: null, navigationType: "PUSH" })).toBe(false);
  });

  it("leaves back and forward to the browser's own scroll restoration", () => {
    expect(decide({ navigationType: "POP" })).toBe(false);
  });

  it("leaves an address with a fragment to the page that owns the target", () => {
    expect(decide({ hash: "#bj-legal-gmail" })).toBe(false);
    expect(decide({ previousPathname: "/", pathname: "/privacy", hash: "#bj-legal-contact" })).toBe(false);
  });

  it("does not scroll when only the query string or the fragment changed, on the same path", () => {
    expect(decide({ previousPathname: "/applications", pathname: "/applications" })).toBe(false);
    expect(decide({ previousPathname: "/privacy", pathname: "/privacy", hash: "#bj-legal-gmail" })).toBe(false);
    expect(decide({ previousPathname: "/privacy", pathname: "/privacy", navigationType: "REPLACE" })).toBe(false);
  });
});
