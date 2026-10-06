import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { anchorsOf, textOfMarkup } from "../testing/markup";
import { ConsentNotice } from "./ConsentNotice";
import { LegalLinks } from "./LegalLinks";

// The two links (Privacy Policy, Terms of Service) and the one consent line the sign-in page
// carries in both of its forms.

function html(element: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{element}</MemoryRouter>);
}

describe("LegalLinks", () => {
  it("is a labelled navigation to the two legal pages, in this order", () => {
    const markup = html(<LegalLinks />);
    expect(markup).toContain('<nav class="bj-legal-links" aria-label="Legal">');
    expect(anchorsOf(markup).map((a) => [a.attributes.href, a.text])).toEqual([
      ["/privacy", "Privacy Policy"],
      ["/terms", "Terms of Service"],
    ]);
  });

  it("opens in the same tab by default (the shell and the footers)", () => {
    for (const anchor of anchorsOf(html(<LegalLinks />))) {
      expect(anchor.attributes.target).toBeUndefined();
      expect(anchor.attributes["aria-label"]).toBeUndefined();
    }
  });

  it("opens in a new tab, safely and said so, when asked (the sign-in card, which holds a form)", () => {
    for (const anchor of anchorsOf(html(<LegalLinks newTab />))) {
      expect(anchor.attributes.target).toBe("_blank");
      expect(anchor.attributes.rel).toBe("noopener noreferrer");
      // The accessible name still begins with the visible text.
      expect(anchor.attributes["aria-label"]).toBe(`${anchor.text} (opens in a new tab)`);
    }
  });
});

describe("ConsentNotice", () => {
  it("says what both ways of making an account promise, with a link to each document", () => {
    const markup = html(<ConsentNotice />);
    expect(textOfMarkup(markup)).toBe(
      "By creating an account or continuing with Google you agree to the Terms and acknowledge the Privacy Policy.",
    );
    expect(anchorsOf(markup).map((a) => [a.attributes.href, a.text])).toEqual([
      ["/terms", "Terms"],
      ["/privacy", "Privacy Policy"],
    ]);
  });

  it("opens each document in a new tab, so the half-filled form is still there afterwards", () => {
    for (const anchor of anchorsOf(html(<ConsentNotice />))) {
      expect(anchor.attributes.target).toBe("_blank");
      expect(anchor.attributes.rel).toBe("noopener noreferrer");
      expect(anchor.attributes["aria-label"]).toBe(`${anchor.text} (opens in a new tab)`);
    }
  });

  it("only informs: it has no checkbox, no input and no button that could block anything", () => {
    const markup = html(<ConsentNotice />);
    expect(markup).not.toMatch(/<(input|button|form|label)\b/);
  });
});
