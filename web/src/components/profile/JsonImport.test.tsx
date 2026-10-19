import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { tagsOf, textOfMarkup } from "../../testing/markup";
import { ImportSection } from "./JsonImport";

// The JSON import waits while a resume-file import is under way (a file being read, a draft waiting
// for a decision): the JSON preview replaces the file card, which would drop its review unseen.
describe("ImportSection while a resume-file import is open", () => {
  function renderSection(disabled: boolean) {
    return renderToStaticMarkup(
      <ImportSection replacing busy={false} disabled={disabled} error={null} onImport={() => {}} />,
    );
  }

  function buttonStates(html: string): Record<string, boolean> {
    const states: Record<string, boolean> = {};
    for (const match of html.matchAll(/<button([^>]*)>([^<]*)<\/button>/g)) {
      states[match[2]] = /\bdisabled\b/.test(match[1]);
    }
    return states;
  }

  it("turns off pasting, the upload and the Import button, and says why", () => {
    const html = renderSection(true);
    expect("disabled" in tagsOf(html, "textarea")[0]).toBe(true);
    const states = buttonStates(html);
    expect(states["Upload .json file"]).toBe(true);
    expect(states["Import"]).toBe(true);
    // the template and the prompt need no server, so they stay available
    expect(states["Download blank template"]).toBe(false);
    expect(textOfMarkup(html)).toContain(
      "A file import above is in progress. Finish it, or discard its draft, before importing JSON.",
    );
  });

  it("is as it was when no file import is open: pasting and uploading are on, and no note", () => {
    const html = renderSection(false);
    expect("disabled" in tagsOf(html, "textarea")[0]).toBe(false);
    expect(buttonStates(html)["Upload .json file"]).toBe(false);
    expect(textOfMarkup(html)).not.toContain("A file import above is in progress");
  });
});
