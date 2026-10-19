// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { ReceiptRowInput, TrailInput } from "../../lib/trail";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { click, installViewport, keydown, query, render } from "../../testing/render";
import { BlockTrail, DocumentBlock, DocumentLayout, TrailMargin, useDocumentMode } from "./DocumentLayout";

const DRAFT: TrailInput = { glyph: "model", actor: "Drafted by your model", basis: "on your key", when: "Sep 22" };
const CHECK: TrailInput = { glyph: "code", actor: "Checked by code", basis: "no model involved" };
const DRAFT_RECEIPT: ReceiptRowInput[] = [{ label: "run", value: "a1b2c3d4" }];
const CHECK_RECEIPT: ReceiptRowInput[] = [{ label: "gate", value: "proceed" }];

function Document({ summary }: { summary?: boolean }) {
  return (
    <DocumentLayout summary={summary === true ? <p>Status saved · resume drafted</p> : undefined}>
      <DocumentBlock id="draft" trail={DRAFT} receipt={DRAFT_RECEIPT} receiptTitle="Where the draft came from">
        <h2>Draft</h2>
        <p data-block="draft">The draft block</p>
      </DocumentBlock>
      <DocumentBlock id="check">
        <h2>Check</h2>
        <p data-block="check">The check block</p>
        <BlockTrail trail={CHECK} receipt={CHECK_RECEIPT} receiptTitle="Where the check came from" />
      </DocumentBlock>
      <DocumentBlock id="quiet">
        <p data-block="quiet">A block with nothing to say about where it came from</p>
      </DocumentBlock>
    </DocumentLayout>
  );
}

const triggers = (root: ParentNode) => [...root.querySelectorAll<HTMLButtonElement>("button.bj-trail-trigger")];
const words = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const layout = (root: ParentNode) => query(root, ".bj-doc");
const cells = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>(".bj-doc-margin")];

describe("DocumentLayout at 1280px and up: the trail margin", () => {
  it("puts each block's trail line, as a button, in that block's margin cell", () => {
    installViewport(1280);
    const { container } = render(<Document />);
    expect(layout(container).classList.contains("bj-doc--margin")).toBe(true);
    expect(layout(container).getAttribute("data-document-mode")).toBe("margin");
    const margins = cells(container);
    expect(margins).toHaveLength(3);
    const lines = triggers(container);
    expect(lines).toHaveLength(2);
    expect(margins[0].contains(lines[0])).toBe(true);
    expect(margins[1].contains(lines[1])).toBe(true);
    expect(margins[2].querySelector("button")).toBeNull();
    // Not inside the block's own column.
    for (const line of lines) expect(line.closest(".bj-doc-main")).toBeNull();
    expect(words(lines[0])).toBe("✦ Drafted by your model · on your key · Sep 22");
    for (const line of lines) expect(line.getAttribute("aria-expanded")).toBe("false");
  });

  it("opens a receipt IN the margin, with aria-expanded on the line, and no sheet", () => {
    installViewport(1280);
    const { container } = render(<Document />);
    const [draft] = triggers(container);
    click(draft);
    expect(draft.getAttribute("aria-expanded")).toBe("true");
    const receipt = query(container, ".bj-trail-receipt");
    expect(cells(container)[0].contains(receipt)).toBe(true);
    expect(receipt.closest(".bj-doc-main")).toBeNull();
    expect(query(receipt, '[role="group"]').getAttribute("aria-label")).toBe("Where the draft came from");
    expect(words(receipt)).toContain("a1b2c3d4");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(draft.getAttribute("aria-controls")).toBe(receipt.id);
    click(draft);
    expect(draft.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".bj-trail-receipt")).toBeNull();
  });

  it("keeps the reading order: a block, then its own trail line, then the next block", () => {
    installViewport(1280);
    const { container } = render(<Document />);
    const order = [...container.querySelectorAll("[data-block], button.bj-trail-trigger")].map((element) =>
      element instanceof HTMLButtonElement ? `trail:${words(element).split(" · ")[0]}` : `block:${element.getAttribute("data-block")}`,
    );
    expect(order).toEqual(["block:draft", "trail:✦ Drafted by your model", "block:check", "trail:○ Checked by code", "block:quiet"]);
  });

  it("leaves the margin a sibling AFTER its block's content, so no block ever follows its neighbour's margin out of order", () => {
    installViewport(1280);
    const { container } = render(<Document />);
    for (const block of container.querySelectorAll(".bj-doc-block")) {
      const [main, margin] = [...block.children];
      expect(main.className).toBe("bj-doc-main");
      expect(margin.className).toBe("bj-doc-margin");
    }
  });

  it("holds one open receipt for the whole document", () => {
    installViewport(1280);
    const { container } = render(<Document />);
    const [draft, check] = triggers(container);
    click(draft);
    click(check);
    expect(draft.getAttribute("aria-expanded")).toBe("false");
    expect(check.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelectorAll(".bj-trail-receipt")).toHaveLength(1);
    expect(cells(container)[1].querySelector(".bj-trail-receipt")).not.toBeNull();
  });

  it("closes an open receipt on Esc and puts focus back on its line", () => {
    installViewport(1280);
    const { container } = render(<Document />);
    const [draft] = triggers(container);
    click(draft);
    query<HTMLElement>(container, ".bj-receipt").focus();
    expect(keydown("Escape")).toBe(false);
    expect(draft.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(draft);
  });

  it("ignores Esc while nothing is open, and other keys while one is", () => {
    installViewport(1280);
    const { container } = render(<Document />);
    const [draft] = triggers(container);
    draft.focus();
    expect(keydown("Escape")).toBe(true);
    click(draft);
    keydown("a");
    expect(draft.getAttribute("aria-expanded")).toBe("true");
  });

  it("shows a trail with no receipt as plain words in the margin, not a button", () => {
    installViewport(1280);
    const { container } = render(
      <DocumentLayout>
        <DocumentBlock trail={CHECK}>
          <p>Block</p>
        </DocumentBlock>
      </DocumentLayout>,
    );
    expect(triggers(container)).toHaveLength(0);
    expect(words(query(cells(container)[0], ".bj-trail--static"))).toBe("○ Checked by code · no model involved");
  });

  it("draws no summary", () => {
    installViewport(1280);
    const { container } = render(<Document summary />);
    expect(container.querySelector(".bj-doc-summary")).toBeNull();
  });

  it("has no serious accessibility violations, closed and with a receipt open", async () => {
    installViewport(1280);
    const { container } = render(
      <main>
        <Document />
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
    click(triggers(container)[0]);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("DocumentLayout from 960px to 1279px: inline, with the summary", () => {
  it("draws no margin: each trail line sits inside its block, after the block's own content", () => {
    installViewport(1024);
    const { container } = render(<Document />);
    expect(layout(container).classList.contains("bj-doc--inline")).toBe(true);
    expect(cells(container)).toHaveLength(0);
    const lines = triggers(container);
    expect(lines).toHaveLength(2);
    expect(lines[0].closest(".bj-doc-main")?.querySelector('[data-block="draft"]')).not.toBeNull();
    expect(query(container, '[data-block="draft"]').compareDocumentPosition(lines[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(lines[0].compareDocumentPosition(query(container, '[data-block="check"]')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("opens the receipt inline under the line, as a disclosure, not a sheet", () => {
    installViewport(1024);
    const { container } = render(<Document />);
    const [draft] = triggers(container);
    click(draft);
    expect(draft.getAttribute("aria-expanded")).toBe("true");
    const receipt = query(container, ".bj-trail-receipt");
    expect(receipt.closest(".bj-doc-main")).not.toBeNull();
    expect(draft.closest(".bj-trail-block")?.contains(receipt)).toBe(true);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    keydown("Escape", { target: query(container, ".bj-receipt") });
    expect(draft.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(draft);
  });

  it("holds one open receipt for the whole document", () => {
    installViewport(1024);
    const { container } = render(<Document />);
    const [draft, check] = triggers(container);
    click(draft);
    click(check);
    expect(draft.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelectorAll(".bj-trail-receipt")).toHaveLength(1);
  });

  it("shows the page's current-state summary once, before the first block, as a labelled group", () => {
    installViewport(1024);
    const { container } = render(<Document summary />);
    const summary = query(container, ".bj-doc-summary");
    expect(summary.getAttribute("role")).toBe("group");
    expect(summary.getAttribute("aria-label")).toBe("Current state");
    expect(words(summary)).toBe("Status saved · resume drafted");
    expect(container.querySelectorAll(".bj-doc-summary")).toHaveLength(1);
    expect(summary.compareDocumentPosition(query(container, "#draft")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("draws no summary when the page gave none, and takes the summary's name from the page", () => {
    installViewport(1024);
    const { container, rerender } = render(<Document />);
    expect(container.querySelector(".bj-doc-summary")).toBeNull();
    rerender(
      <DocumentLayout summary={<p>Saved</p>} summaryLabel="Where this application stands">
        <DocumentBlock>
          <p>Block</p>
        </DocumentBlock>
      </DocumentLayout>,
    );
    expect(query(container, ".bj-doc-summary").getAttribute("aria-label")).toBe("Where this application stands");
  });

  it("has no serious accessibility violations with the summary and a receipt open", async () => {
    installViewport(1024);
    const { container } = render(
      <main>
        <Document summary />
      </main>,
    );
    click(triggers(container)[0]);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("DocumentLayout under 960px: always inline, receipts in the sheet", () => {
  it("keeps the trail line under its block and opens the receipt as a focus-trapped dialog", () => {
    installViewport(375);
    const { container } = render(<Document />);
    expect(layout(container).classList.contains("bj-doc--sheet")).toBe(true);
    expect(cells(container)).toHaveLength(0);
    const [draft] = triggers(container);
    expect(draft.closest(".bj-doc-main")).not.toBeNull();
    expect(draft.getAttribute("aria-haspopup")).toBe("dialog");
    click(draft);
    const sheet = query(document.body, '[role="dialog"]');
    expect(sheet.getAttribute("aria-modal")).toBe("true");
    expect(sheet.getAttribute("aria-label") ?? words(query(sheet, "h2"))).toBe("Where the draft came from");
    expect(words(sheet)).toContain("a1b2c3d4");
    expect(container.querySelector(".bj-trail-receipt")).toBeNull();
    expect(document.activeElement).toBe(query(sheet, 'button[aria-label="Close"]'));
  });

  it("closes on Esc and returns focus to the line that opened it", () => {
    installViewport(375);
    const { container } = render(<Document />);
    const [draft] = triggers(container);
    click(draft);
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(draft.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(draft);
  });

  it("draws no summary", () => {
    installViewport(375);
    const { container } = render(<Document summary />);
    expect(container.querySelector(".bj-doc-summary")).toBeNull();
  });

  it("has no serious accessibility violations with the sheet open", async () => {
    installViewport(375);
    const { container } = render(
      <main>
        <Document />
      </main>,
    );
    click(triggers(container)[0]);
    await expectNoSeriousA11yViolations(document.body);
  });
});

describe("switching width", () => {
  it("moves the same trail lines between margin, inline and sheet without the page doing anything", () => {
    const viewport = installViewport(1280);
    const { container } = render(<Document summary />);
    expect(cells(container)).toHaveLength(3);
    viewport.setWidth(1100);
    expect(cells(container)).toHaveLength(0);
    expect(triggers(container)).toHaveLength(2);
    expect(container.querySelectorAll(".bj-doc-summary")).toHaveLength(1);
    viewport.setWidth(500);
    expect(layout(container).classList.contains("bj-doc--sheet")).toBe(true);
    expect(container.querySelector(".bj-doc-summary")).toBeNull();
    viewport.setWidth(1400);
    expect(cells(container)).toHaveLength(3);
    expect(triggers(container)).toHaveLength(2);
  });

  it("closes an open receipt when the placement changes, so it never reappears as a sheet that takes focus", () => {
    const viewport = installViewport(1280);
    const { container } = render(<Document />);
    click(triggers(container)[0]);
    expect(container.querySelector(".bj-trail-receipt")).not.toBeNull();
    viewport.setWidth(500);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(triggers(container)[0].getAttribute("aria-expanded")).toBe("false");
    viewport.setWidth(1100);
    expect(container.querySelector(".bj-trail-receipt")).toBeNull();
  });

  it("uses 1280 and 960 as the exact edges", () => {
    let latest = "";
    function Probe() {
      latest = useDocumentMode();
      return null;
    }
    const viewport = installViewport(959);
    render(<Probe />);
    const modes = [959, 960, 1279, 1280, 959].map((width) => {
      viewport.setWidth(width);
      return latest;
    });
    expect(modes).toEqual(["sheet", "inline", "inline", "margin", "sheet"]);
  });
});

describe("BlockTrail and DocumentBlock outside the layout", () => {
  it("is a plain trail line that opens its own receipt", () => {
    installViewport(1280);
    const { container } = render(<BlockTrail trail={DRAFT} receipt={DRAFT_RECEIPT} />);
    click(query(container, "button.bj-trail-trigger"));
    expect(container.querySelector(".bj-trail-receipt")).not.toBeNull();
  });

  it("is a wrapper with no margin cell", () => {
    installViewport(1280);
    const { container } = render(
      <DocumentBlock id="alone" trail={DRAFT}>
        <p>Content</p>
      </DocumentBlock>,
    );
    expect(cells(container)).toHaveLength(0);
    expect(query(container, "#alone p").textContent).toBe("Content");
    expect(triggers(container)).toHaveLength(0);
    expect(query(container, ".bj-trail--static")).not.toBeNull();
  });

  it("draws a trail given to a margin cell by hand, and falls back inline for a block with no DocumentBlock around it", () => {
    installViewport(1280);
    const { container } = render(
      <DocumentLayout>
        <BlockTrail trail={DRAFT} receipt={DRAFT_RECEIPT} />
        <DocumentBlock>
          <TrailMargin />
        </DocumentBlock>
      </DocumentLayout>,
    );
    // The loose trail has no block to sit beside, so it is drawn where it was written.
    expect(triggers(container)).toHaveLength(1);
    expect(triggers(container)[0].closest(".bj-doc-margin")).toBeNull();
  });
});

describe("a block that goes away or has nothing to say", () => {
  it("lets the next receipt open normally after a block that had one open is removed", () => {
    installViewport(1280);
    const { container, rerender } = render(<Document />);
    click(triggers(container)[0]);
    rerender(
      <DocumentLayout>
        <DocumentBlock trail={CHECK} receipt={CHECK_RECEIPT}>
          <p>Only block</p>
        </DocumentBlock>
      </DocumentLayout>,
    );
    expect(container.querySelector(".bj-trail-receipt")).toBeNull();
    const [only] = triggers(container);
    expect(only.getAttribute("aria-expanded")).toBe("false");
    click(only);
    expect(only.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelectorAll(".bj-trail-receipt")).toHaveLength(1);
  });

  it("keeps another block's receipt open when a block that had none open goes away", () => {
    installViewport(1280);
    const Two = ({ withFirst }: { withFirst: boolean }) => (
      <DocumentLayout>
        {withFirst && (
          <DocumentBlock trail={DRAFT} receipt={DRAFT_RECEIPT}>
            <p>First</p>
          </DocumentBlock>
        )}
        <DocumentBlock trail={CHECK} receipt={CHECK_RECEIPT}>
          <p>Second</p>
        </DocumentBlock>
      </DocumentLayout>
    );
    const { container, rerender } = render(<Two withFirst />);
    click(triggers(container)[1]);
    expect(container.querySelectorAll(".bj-trail-receipt")).toHaveLength(1);
    rerender(<Two withFirst={false} />);
    expect(triggers(container)).toHaveLength(1);
    expect(triggers(container)[0].getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelectorAll(".bj-trail-receipt")).toHaveLength(1);
  });

  it("draws no line and no empty button for a trail with no words left (the omit rule)", () => {
    installViewport(1280);
    const { container } = render(
      <DocumentLayout>
        <DocumentBlock trail={{ glyph: "model", actor: null, basis: undefined, when: "" }} receipt={DRAFT_RECEIPT}>
          <p>Block</p>
        </DocumentBlock>
      </DocumentLayout>,
    );
    expect(container.querySelector(".bj-trail")).toBeNull();
    expect(triggers(container)).toHaveLength(0);
  });
});
