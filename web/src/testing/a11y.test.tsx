// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { expectNoSeriousA11yViolations, seriousA11yViolations } from "./a11y";
import { render } from "./render";

describe("the accessibility helper", () => {
  it("passes markup with named controls and labelled landmarks", async () => {
    const { container } = render(
      <main>
        <h1>Title</h1>
        <button type="button">Save</button>
        <nav aria-label="Breadcrumb">
          <a href="/x">Home</a>
        </nav>
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });

  it("fails on a serious or critical violation and names it", async () => {
    const { container } = render(
      <div>
        <button type="button" />
        <img src="/x.png" />
      </div>,
    );
    const violations = await seriousA11yViolations(container);
    expect(violations.map((violation) => violation.id).sort()).toEqual(["button-name", "image-alt"]);
    await expect(expectNoSeriousA11yViolations(container)).rejects.toThrow(/button-name/);
  });

  it("does not fail on a moderate one, such as a page with no landmark", async () => {
    const { container } = render(<p>Just a paragraph</p>);
    await expectNoSeriousA11yViolations(container);
  });
});
