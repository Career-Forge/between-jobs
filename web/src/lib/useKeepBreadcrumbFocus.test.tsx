// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { settle } from "../testing/forms";
import { query, render } from "../testing/render";
import { useKeepBreadcrumbFocus } from "./useKeepBreadcrumbFocus";

// A page whose loading frame and finished page are different trees has a new breadcrumb link when the page
// arrives. The hook hands a keyboard that was on the old link to the new one, and does nothing else.

function Page({ frame, rebuild = true }: { frame: string; rebuild?: boolean }) {
  useKeepBreadcrumbFocus(frame);
  return (
    <div>
      <nav className="bj-breadcrumb">
        <a key={rebuild ? frame : "same"} href="#applications">
          Applications
        </a>
        <a href="#current">Current</a>
      </nav>
      <input aria-label="Elsewhere" />
    </div>
  );
}

describe("useKeepBreadcrumbFocus", () => {
  it("hands focus to the new breadcrumb link when the keyboard was on the old one and the page replaced it", async () => {
    const { container, rerender } = render(<Page frame="frame" />);
    const old = query<HTMLAnchorElement>(container, ".bj-breadcrumb a");
    old.focus();
    expect(document.activeElement).toBe(old);
    rerender(<Page frame="page" />);
    await settle();
    expect(container.contains(old)).toBe(false);
    expect(document.activeElement).toBe(query(container, ".bj-breadcrumb a"));
    expect(document.activeElement?.textContent).toBe("Applications");
  });

  it("does nothing when the keyboard was somewhere else", async () => {
    const { container, rerender } = render(<Page frame="frame" />);
    query<HTMLInputElement>(container, "input").focus();
    rerender(<Page frame="page" />);
    await settle();
    expect(document.activeElement).toBe(query(container, "input"));
  });

  it("does nothing when the person moved off the breadcrumb before the page arrived", async () => {
    const { container, rerender } = render(<Page frame="frame" />);
    const link = query<HTMLAnchorElement>(container, ".bj-breadcrumb a");
    link.focus();
    link.blur();
    await settle();
    rerender(<Page frame="page" />);
    await settle();
    expect(document.activeElement).toBe(document.body);
  });

  it("does not take focus from something else that already has it, even a breadcrumb link that was not replaced", async () => {
    const { container, rerender } = render(<Page frame="frame" rebuild={false} />);
    const current = query<HTMLAnchorElement>(container, '.bj-breadcrumb a[href="#current"]');
    current.focus();
    rerender(<Page frame="page" rebuild={false} />);
    await settle();
    expect(document.activeElement).toBe(current);
  });
});
