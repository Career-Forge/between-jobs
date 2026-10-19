// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { ProfileVersion } from "../../lib/profileTypes";
import { buttonNamed, click, render } from "../../testing/render";
import { JsonReview } from "./JsonReview";

// The two buttons of the preview start a request, so they must not disable themselves while it is out: a disabled
// button drops keyboard focus to the top of the page, and a refused activation would leave it there.

const VERSION = { id: "v1", canonical_json: { personal: { name: "Asha Verma" } }, activated_at: null, created_at: "2026-10-19T11:55:00Z", source_kind: "web_json_paste", version_count: 2 } as unknown as ProfileVersion;

describe("the preview's buttons while a request is out", () => {
  it("keep keyboard focus, say nothing is possible, and ignore a press", () => {
    const onActivate = vi.fn();
    const onCancel = vi.fn();
    const { container, rerender } = render(<JsonReview version={VERSION} busy={false} error={null} onActivate={onActivate} onCancel={onCancel} />);
    const activate = buttonNamed(container, "Looks good -- activate");
    activate.focus();
    click(activate);
    expect(onActivate).toHaveBeenCalledTimes(1);

    rerender(<JsonReview version={VERSION} busy error={null} onActivate={onActivate} onCancel={onCancel} />);
    expect(document.activeElement).toBe(activate);
    for (const name of ["Looks good -- activate", "Cancel"]) {
      const button = buttonNamed(container, name);
      expect(button.disabled, name).toBe(false);
      expect(button.getAttribute("aria-disabled"), name).toBe("true");
      click(button);
    }
    expect(onActivate).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });
});
