// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CanonicalProfile } from "../../lib/profileTypes";
import { calls, reset } from "../../testing/fakeApi";
import { settle, text } from "../../testing/forms";
import { render } from "../../testing/render";
import { ResumeSections, type ResumeSectionsProps } from "./ResumeSections";

// The resume part's Coverage block, with the real Tailor panel and the API faked: a posting with no text
// is offered no "Check coverage" button, and so makes no request. The API stores "(no description
// available)" for a tracked job that came with no text; that must read as no text, or a press would send
// it to the model as if it were the posting. The Studio and Generate panels are stood in, since they are not
// what is under test. All text is synthetic.

vi.mock("../../lib/api", async () => (await import("../../testing/fakeApi")).apiModule);
vi.mock("../HeaderComposer", () => ({ HeaderComposer: () => <div>Header composer</div> }));
vi.mock("../SectionOrderEditor", () => ({ SectionOrderEditor: () => <div>Section order editor</div> }));
vi.mock("../ShapeSettingsPanel", () => ({ ShapeSettingsPanel: () => <div>Shape settings</div> }));
vi.mock("../GeneratePanel", () => ({ GeneratePanel: () => <div>Generate panel</div> }));

const PROFILE = { personal: { name: "Reader Example" } } as unknown as CanonicalProfile;
const NO_TEXT_LINE = "There is no posting text to check your profile against.";
const NONE_SAVED = "No description text was saved for this posting, so tailoring has nothing to read.";

function sections(descriptionText: string | undefined | null) {
  const props = {
    applicationId: "a1",
    descriptionText,
    profile: PROFILE,
    modelSetup: null,
    generate: {},
  } as unknown as ResumeSectionsProps;
  return render(
    <MemoryRouter>
      <ResumeSections {...props} />
    </MemoryRouter>,
  );
}

const checkButtons = (container: HTMLElement) => [...container.querySelectorAll("button")].filter((button) => /check coverage/i.test(button.textContent ?? ""));

beforeEach(() => reset());
afterEach(() => vi.restoreAllMocks());

describe("Coverage with no posting text", () => {
  it.each([
    ["empty", ""],
    ["only blanks", "  \n\t "],
    ["null", null],
    ["the API's placeholder", "(no description available)"],
    ["the placeholder, shouted and padded", "  (NO DESCRIPTION AVAILABLE)  "],
    ["the placeholder without its parentheses", "No description available."],
  ])("offers no Check coverage button and makes no request for %s", async (_name, descriptionText) => {
    const { container } = sections(descriptionText);
    await settle();
    expect(checkButtons(container)).toEqual([]);
    expect(text(container)).toContain(NO_TEXT_LINE);
    expect(text(container)).toContain(NONE_SAVED);
    expect(text(container)).not.toContain("Pressing Check coverage");
    expect(calls).toEqual([]);
  });

  it("offers the button, and not the line, for a posting that has text, and still asks nothing until it is pressed", async () => {
    const { container } = sections("Build and run batch pipelines.");
    await settle();
    expect(checkButtons(container)).toHaveLength(1);
    expect(text(container)).not.toContain(NO_TEXT_LINE);
    expect(text(container)).not.toContain(NONE_SAVED);
    expect(calls).toEqual([]);
  });

  it("offers the button for a real posting that merely mentions the words", async () => {
    const { container } = sections("(no description available) -- see the careers page for the full text of this role.");
    await settle();
    expect(checkButtons(container)).toHaveLength(1);
  });
});
