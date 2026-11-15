// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { text } from "../../testing/forms";
import { render } from "../../testing/render";
import { PostingBlock, postingBody } from "./PostingBlock";

// The page's one rule for "this posting has text". The API stores "(no description available)" as the
// description of a tracked job that came with none, so that sentence must read as no text. All text is
// synthetic.

const NONE = "No description text was saved for this posting, so tailoring has nothing to read.";

describe("postingBody", () => {
  it.each([
    ["undefined", undefined],
    ["null", null],
    ["empty", ""],
    ["only blanks", " \n\t "],
  ])("is empty for %s", (_name, input) => {
    expect(postingBody(input)).toBe("");
  });

  it("trims a real description and keeps its inner text as it came", () => {
    expect(postingBody("  Build and run\n batch pipelines.  ")).toBe("Build and run\n batch pipelines.");
  });

  it.each([
    "(no description available)",
    "  (no description available)  ",
    "(No Description Available)",
    "(NO DESCRIPTION AVAILABLE)",
    "no description available",
    "No description available.",
    "(no description available).",
    "(no description available.)",
    "\n(no description available)\n",
    "(no  description\tavailable)",
  ])("is empty for the API's placeholder, %j", (input) => {
    expect(postingBody(input)).toBe("");
  });

  it.each([
    "(no description available) -- see the careers page",
    "Sorry, no description available for this role.",
    "(no description available",
    "no description available)",
    "((no description available))",
    "(no description)",
    "No description available yet: Build and run batch pipelines.",
  ])("keeps a text that only mentions or contains the words, %j", (input) => {
    expect(postingBody(input)).toBe(input.trim());
  });
});

describe("PostingBlock", () => {
  it.each(["(no description available)", " (No Description Available) ", "no description available."])(
    "says no text was saved, and draws no well or character count, for the placeholder %j",
    (placeholder) => {
      const { container } = render(<PostingBlock text={placeholder} />);
      expect(text(container)).toContain(NONE);
      expect(container.querySelector("[aria-label='Job description text']")).toBeNull();
      expect(text(container)).not.toContain("Tailoring reads only this text");
      expect(text(container)).not.toContain("no description available");
    },
  );

  it("draws a real description whole, with its length", () => {
    const { container } = render(<PostingBlock text="  Build and run batch pipelines.  " />);
    expect(text(container)).not.toContain(NONE);
    expect(text(container.querySelector("[aria-label='Job description text']")!)).toBe("Build and run batch pipelines.");
    expect(text(container)).toContain("Tailoring reads only this text (30 characters).");
  });
});
