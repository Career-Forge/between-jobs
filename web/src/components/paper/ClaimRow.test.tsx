// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { findForbiddenWord } from "../../lib/trail";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { click, query, render } from "../../testing/render";
import { CLAIM_CHIPS, ClaimList, ClaimRow, type ClaimStatus } from "./ClaimRow";

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const STATUSES: ClaimStatus[] = ["supported", "unverifiable", "contradicted", "unchecked"];

describe("ClaimRow", () => {
  it("draws the chip, the claim in curly quotes, the reason and the actions, in that order", () => {
    const { container } = render(
      <ul>
        <ClaimRow
          status="unverifiable"
          claim="Reduced query costs by 40%"
          reason="None of the achievements picked for this draft mention query costs."
          actions={<button type="button">Copy claim</button>}
        />
      </ul>,
    );
    const row = query(container, "li.bj-claimrow");
    expect(text(query(row, ".bj-claimrow-chip"))).toBe("◆ Unverifiable");
    expect(text(query(row, ".bj-claimrow-claim"))).toBe("“Reduced query costs by 40%”");
    expect(text(query(row, ".bj-claimrow-reason"))).toBe("None of the achievements picked for this draft mention query costs.");
    expect(text(query(row, ".bj-claimrow-actions"))).toBe("Copy claim");
    const order = [".bj-claimrow-chip", ".bj-claimrow-claim", ".bj-claimrow-reason", ".bj-claimrow-actions"].map((selector) => query(row, selector));
    for (let index = 1; index < order.length; index++) {
      expect(order[index - 1].compareDocumentPosition(order[index]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("says each status in a word and a different mark, so color is never the only difference", () => {
    const words: string[] = [];
    const marks = new Map<string, string>();
    for (const status of STATUSES) {
      const { container, unmount } = render(<ClaimRow as="div" status={status} claim="A claim" />);
      const chip = query(container, ".bj-claimrow-chip");
      words.push(text(chip).replace(/^\S+\s/, ""));
      marks.set(status, query(chip, "[aria-hidden]").textContent ?? "");
      expect(chip.classList.contains(`bj-claimrow-chip--${status}`)).toBe(true);
      unmount();
    }
    expect(words).toEqual(["Supported", "Unverifiable", "Contradicted", "Not checked"]);
    // Contradicted and unchecked each have a mark of their own; the two gold ones differ by their word.
    expect(marks.get("contradicted")).toBe("✕");
    expect(marks.get("unchecked")).toBe("○");
    expect(marks.get("supported")).toBe("◆");
    expect(marks.get("unverifiable")).toBe("◆");
    expect(new Set(words).size).toBe(4);
  });

  it("draws the mark aria-hidden and lets the word carry the meaning", () => {
    const { container } = render(<ClaimRow as="div" status="contradicted" claim="A claim" />);
    const chip = query(container, ".bj-claimrow-chip");
    expect(query(chip, "[aria-hidden='true']").textContent).toBe("✕");
    expect(chip.textContent?.replace("✕", "").trim()).toBe("Contradicted");
  });

  it("takes its marks from the shared trail glyph set: ◆ evidence, ✕ failed, ○ not checked", () => {
    expect(CLAIM_CHIPS.contradicted.glyph).toBe("failed");
    expect(CLAIM_CHIPS.unchecked.glyph).toBe("code");
    expect(CLAIM_CHIPS.unverifiable.glyph).toBe("evidence");
    expect(CLAIM_CHIPS.supported.glyph).toBe("evidence");
  });

  it("never says 'verified' about a claim, and never uses the check mark, which is for a deterministic pass", () => {
    for (const status of STATUSES) {
      const { container, unmount } = render(<ClaimRow as="div" status={status} claim="A claim" />);
      expect(findForbiddenWord(text(container))).toBeNull();
      expect(container.textContent).not.toContain("✓");
      unmount();
    }
  });

  it("is never green: no chip class carries the emerald tone", () => {
    for (const status of STATUSES) {
      const { container, unmount } = render(<ClaimRow as="div" status={status} claim="A claim" />);
      expect(container.querySelector(".bj-trail-glyph--emerald")).toBeNull();
      unmount();
    }
  });

  it("does not add quotation marks to a claim that is a quotation already, and does not reword it", () => {
    const { container } = render(<ClaimRow as="div" status="unchecked" claim={"Led a team of 12 -- \"and shipped\""} />);
    expect(text(query(container, ".bj-claimrow-claim"))).toBe("“Led a team of 12 -- \"and shipped\"”");
  });

  it("leaves out the reason and the actions it was not given", () => {
    const { container } = render(<ClaimRow as="div" status="unchecked" claim="A claim" />);
    expect(container.querySelector(".bj-claimrow-reason")).toBeNull();
    expect(container.querySelector(".bj-claimrow-actions")).toBeNull();
  });

  it("is a div when asked to be, and an li by default", () => {
    const { container, unmount } = render(<ClaimRow as="div" status="unchecked" claim="A claim" />);
    expect(query(container, ".bj-claimrow").tagName).toBe("DIV");
    unmount();
    const second = render(
      <ul>
        <ClaimRow status="unchecked" claim="A claim" />
      </ul>,
    );
    expect(query(second.container, ".bj-claimrow").tagName).toBe("LI");
  });

  it("keeps its actions reachable by keyboard in reading order, after the claim", () => {
    let copied = 0;
    const { container } = render(
      <ul>
        <ClaimRow
          status="unverifiable"
          claim="A claim"
          actions={
            <>
              <a href="/profile">If it&apos;s true, add it to your profile</a>
              <button type="button" onClick={() => (copied += 1)}>
                Copy claim
              </button>
            </>
          }
        />
      </ul>,
    );
    const stops = [...container.querySelectorAll<HTMLElement>("a, button")];
    expect(stops.map(text)).toEqual(["If it's true, add it to your profile", "Copy claim"]);
    click(stops[1]);
    expect(copied).toBe(1);
  });

  it("has no serious accessibility violations in a list of every status", async () => {
    const { container } = render(
      <main>
        <ClaimList label="Flagged claims">
          {STATUSES.map((status) => (
            <ClaimRow key={status} status={status} claim={`A ${status} claim`} reason="Why." actions={<button type="button">Copy claim</button>} />
          ))}
        </ClaimList>
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});

describe("ClaimList", () => {
  it("is a list named by the page", () => {
    const { container } = render(
      <ClaimList label="Flagged claims">
        <ClaimRow status="unverifiable" claim="One" />
        <ClaimRow status="contradicted" claim="Two" />
      </ClaimList>,
    );
    const list = query(container, "ul");
    expect(list.getAttribute("aria-label")).toBe("Flagged claims");
    expect(list.querySelectorAll("li")).toHaveLength(2);
  });
});
