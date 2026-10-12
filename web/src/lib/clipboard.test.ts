import { afterEach, describe, expect, it, vi } from "vitest";
import { copyToClipboard } from "./clipboard";

// The three ways the clipboard can be: there and willing, there and refusing (permission denied),
// and not there at all (a page that is not a secure context). None may throw.

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("copyToClipboard", () => {
  it("writes the text and says it worked when the clipboard allows it", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    await expect(copyToClipboard("Subject: Hello\n\nBody")).resolves.toBe(true);

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith("Subject: Hello\n\nBody");
  });

  it("says it did not work, and does not throw, when the browser refuses", async () => {
    const writeText = vi.fn(async () => {
      throw new DOMException("Write permission denied.", "NotAllowedError");
    });
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    await expect(copyToClipboard("text")).resolves.toBe(false);
    expect(writeText).toHaveBeenCalledTimes(1);
  });

  it("says it did not work, and does not throw, when there is no clipboard", async () => {
    vi.stubGlobal("navigator", { clipboard: undefined });

    await expect(copyToClipboard("text")).resolves.toBe(false);
  });

  it("says it did not work when there is no navigator at all", async () => {
    vi.stubGlobal("navigator", undefined);

    await expect(copyToClipboard("text")).resolves.toBe(false);
  });
});
