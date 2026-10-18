import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import appCss from "../app.css?raw";
import { declarationsFor, withoutMedia } from "../testing/css";
import { ViewToggle } from "./ViewToggle";

const options = [
  { value: "list", label: "List" },
  { value: "board", label: "Board" },
] as const;

describe("ViewToggle", () => {
  it("is a labelled group of buttons that announces exactly the chosen one as pressed", () => {
    const html = renderToStaticMarkup(<ViewToggle label="View" options={options} value="board" onChange={() => undefined} />);
    expect(html).toContain('role="group" aria-label="View"');
    expect(html).toMatch(/type="button" aria-pressed="false"[^>]*>List</);
    expect(html).toMatch(/type="button" aria-pressed="true"[^>]*>Board</);
  });

  it("leaves no class to carry the selection: the stylesheet draws it from aria-pressed alone", () => {
    const html = renderToStaticMarkup(<ViewToggle label="View" options={options} value="list" onChange={() => undefined} />);
    expect(html).not.toContain("bj-toggle-active");
    expect(appCss).not.toContain("bj-toggle-active");
    expect(declarationsFor(withoutMedia(appCss), '.bj-view-toggle button[aria-pressed="true"]').background).toBe("var(--bj-selected-bg)");
  });
});
