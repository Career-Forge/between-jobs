import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { tagsOf } from "../testing/markup";
import { PublicLayout } from "./PublicLayout";
import { MAIN_CONTENT_ID } from "./SkipLink";

describe("the public layout's way in", () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <PublicLayout>
        <h1>Page</h1>
      </PublicLayout>
    </MemoryRouter>,
  );

  it("starts with the skip link, ahead of the header's links, aimed at the one <main>", () => {
    expect(markup.indexOf('class="bj-skip-link"')).toBeGreaterThan(-1);
    expect(markup.indexOf('class="bj-skip-link"')).toBeLessThan(markup.indexOf("<header"));
    expect(markup).toContain(`href="#${MAIN_CONTENT_ID}"`);
    expect(tagsOf(markup, "main")).toHaveLength(1);
    expect(tagsOf(markup, "main")[0]).toMatchObject({ id: MAIN_CONTENT_ID, tabindex: "-1" });
  });
});
