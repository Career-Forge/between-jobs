// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CONVERSION_PROMPT, RESUME_TEMPLATE } from "../../lib/template";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { settle, text, typeInto } from "../../testing/forms";
import { tagsOf, textOfMarkup } from "../../testing/markup";
import { buttonNamed, click, query, render } from "../../testing/render";
import { ImportSection } from "./JsonImport";

const download = vi.hoisted(() => ({ downloadJson: vi.fn() }));
vi.mock("../../lib/profileDownload", async (importOriginal) => ({ ...(await importOriginal<typeof import("../../lib/profileDownload")>()), downloadJson: download.downloadJson }));

afterEach(() => {
  download.downloadJson.mockReset();
  vi.unstubAllGlobals();
});

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

// What the import offers: the template, the conversion prompt, an upload and a paste box, and an Import that
// is only ever pressed on purpose.
describe("ImportSection", () => {
  const section = (props: Partial<Parameters<typeof ImportSection>[0]> = {}) => render(<ImportSection replacing={false} busy={false} disabled={false} error={null} onImport={() => undefined} {...props} />);

  it("is titled for what it does: set up a profile for a person with none, a new version for one with a profile", () => {
    expect(text(query(section().container, "h2"))).toBe("Set up your profile");
    expect(text(query(section({ replacing: true }).container, "h2"))).toBe("Import a new version");
  });

  it("downloads the blank template, which is the fixed one", () => {
    const { container } = section();
    click(buttonNamed(container, "Download blank template"));
    expect(download.downloadJson).toHaveBeenCalledWith(RESUME_TEMPLATE, "between-jobs-template.json");
  });

  it("copies the conversion prompt and says so, and says when the browser refused", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText: write } });
    const { container } = section();
    click(buttonNamed(container, "Copy conversion prompt"));
    await settle();
    expect(write).toHaveBeenCalledWith(CONVERSION_PROMPT);
    expect(text(buttonNamed(container, "Copied ✓"))).toBe("Copied ✓");
    const refused = section();
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) } });
    click(buttonNamed(refused.container, "Copy conversion prompt"));
    await settle();
    expect(text(refused.container)).toContain("Couldn't copy to the clipboard.");
  });

  it("keeps Import off until something is pasted, then sends exactly the pasted text", () => {
    const onImport = vi.fn();
    const { container } = section({ onImport });
    const imports = () => buttonNamed(container, "Import");
    expect(imports().getAttribute("aria-disabled")).toBe("true");
    click(imports());
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "   ");
    expect(imports().getAttribute("aria-disabled")).toBe("true");
    click(imports());
    expect(onImport).not.toHaveBeenCalled();
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), '  {"personal": {}}  ');
    expect(imports().getAttribute("aria-disabled")).toBeNull();
    click(imports());
    expect(onImport).toHaveBeenCalledWith('  {"personal": {}}  ');
  });

  it("sends an uploaded .json file's text at once, and lets the same file be chosen again", async () => {
    const onImport = vi.fn();
    const { container } = section({ onImport });
    const input = query<HTMLInputElement>(container, 'input[type="file"]');
    expect(input.accept).toBe(".json,application/json");
    expect(input.hidden).toBe(true);
    const file = { text: () => Promise.resolve('{"personal":{"name":"A"}}') };
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    expect(onImport).toHaveBeenCalledWith('{"personal":{"name":"A"}}');
    expect(input.value).toBe("");
  });

  // aria-disabled and never disabled: the press is what makes the section busy, and a button that disables
  // itself drops keyboard focus to the top of the page, where a refused import would leave it.
  it("says Importing... and turns the buttons that would start another off while a request is out, keeping focus", () => {
    const onImport = vi.fn();
    const { container, rerender } = render(<ImportSection replacing={false} busy={false} disabled={false} error={null} onImport={onImport} />);
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), '{"personal": {}}');
    const imports = buttonNamed(container, "Import");
    imports.focus();
    rerender(<ImportSection replacing={false} busy disabled={false} error={null} onImport={onImport} />);
    const busy = buttonNamed(container, "Importing...");
    expect(busy).toBe(imports);
    expect(busy.disabled).toBe(false);
    expect(busy.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(busy);
    click(busy);
    expect(onImport).not.toHaveBeenCalled();
    const upload = buttonNamed(container, "Upload .json file");
    expect(upload.disabled).toBe(false);
    expect(upload.getAttribute("aria-disabled")).toBe("true");
  });

  it("shows the importer's refusal as an alert", () => {
    const { container } = section({ error: "personal.name: field required" });
    expect(text(query(container, '[role="alert"]'))).toBe("personal.name: field required");
  });

  it("is never a cyan fill, carries no inline style, and has no serious accessibility violation", async () => {
    const { container } = section();
    expect(container.querySelector(".bj-primary, [style]")).toBeNull();
    await expectNoSeriousA11yViolations(container);
  });
});
