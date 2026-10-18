import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { tagsOf, textOfMarkup } from "../testing/markup";
import { PublicLayout } from "./PublicLayout";
import { Wordmark } from "./Wordmark";

describe("Wordmark", () => {
  const markup = renderToStaticMarkup(<Wordmark />);

  it("reads as 'Between Jobs': the text is the name, and nothing else adds to it", () => {
    expect(textOfMarkup(markup)).toBe("Between Jobs");
    expect(markup).not.toContain("aria-label");
    expect(markup).not.toContain("<svg");
  });

  it("draws the red dot as a hidden span after the name, so a screen reader never meets it", () => {
    const spans = tagsOf(markup, "span");
    expect(spans).toHaveLength(2);
    expect(spans[1]).toMatchObject({ class: "bj-wordmark-dot", "aria-hidden": "true" });
    expect(markup.indexOf("Between Jobs")).toBeLessThan(markup.indexOf("bj-wordmark-dot"));
    expect(markup.match(/<span class="bj-wordmark-dot" aria-hidden="true"><\/span>/g)).toHaveLength(1);
  });

  it("takes an extra class for the places that size it", () => {
    expect(renderToStaticMarkup(<Wordmark className="bj-rail-wordmark" />)).toContain('class="bj-wordmark bj-rail-wordmark"');
  });
});

describe("the public layout's wordmarks", () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <PublicLayout>
        <h1>Page</h1>
      </PublicLayout>
    </MemoryRouter>,
  );

  it("sets the header link and the footer in the wordmark, the link still named 'Between Jobs'", () => {
    expect(markup.match(/class="bj-wordmark"/g)).toHaveLength(2);
    const header = markup.slice(markup.indexOf("<header"), markup.indexOf("</header>"));
    expect(header).toMatch(/<a [^>]*class="bj-public-wordmark"[^>]*href="\/"/);
    expect(textOfMarkup(header)).toContain("Between Jobs");
  });
});
