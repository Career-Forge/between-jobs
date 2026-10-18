// @vitest-environment jsdom
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { findForbiddenWord, mono, type ReceiptRowInput, type TrailInput } from "../../lib/trail";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { buttonNamed, click, installViewport, keydown, query, render } from "../../testing/render";
import { StaticTrailLine, TrailLine } from "./TrailLine";

const TRAIL: TrailInput = {
  glyph: "model",
  actor: "Drafted by your model",
  basis: "on your OpenRouter key",
  when: "Sep 22",
};
const RECEIPT: ReceiptRowInput[] = [
  { label: "run", value: "a1b2c3d4" },
  { label: "pdf", value: "9f8e7d6c" },
  { label: "ATS", value: "64 -> 72" },
];

const trigger = (root: ParentNode) => query<HTMLButtonElement>(root, "button.bj-trail-trigger");
const words = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
// A receipt as its rows: "label value" for a field, the note's own words for a note.
const rowsOf = (well: Element) =>
  [...well.querySelectorAll(".bj-receipt-row, .bj-receipt-note")].map((row) => [...row.children].map((cell) => cell.textContent).join(" ") || row.textContent);

describe("a trail line with a receipt (wide screens)", () => {
  it("is a real button, collapsed, that says what it is without its glyph", () => {
    installViewport(1280);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    const button = trigger(container);
    expect(button.tagName).toBe("BUTTON");
    expect(button.getAttribute("type")).toBe("button");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(words(button)).toBe("✦ Drafted by your model · on your OpenRouter key · Sep 22");
    const hidden = [...button.querySelectorAll('[aria-hidden="true"]')].map((element) => element.textContent);
    expect(hidden).toEqual(["✦"]);
  });

  it("opens the receipt inline under the line, and closes it again", () => {
    installViewport(1280);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    expect(container.querySelector(".bj-receipt")).toBeNull();
    click(trigger(container));
    expect(trigger(container).getAttribute("aria-expanded")).toBe("true");
    const receipt = query(container, ".bj-receipt");
    expect(trigger(container).getAttribute("aria-controls")).toBe(receipt.parentElement?.id);
    expect(rowsOf(receipt)).toEqual(["run a1b2c3d4", "pdf 9f8e7d6c", "ATS 64 -> 72"]);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    click(trigger(container));
    expect(trigger(container).getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".bj-receipt")).toBeNull();
  });

  it("builds the receipt only from fields that were returned", () => {
    installViewport(1280);
    const { container } = render(
      <TrailLine
        trail={TRAIL}
        receipt={[{ label: "run", value: "a1b2c3d4" }, { label: "pdf", value: undefined }, { label: "gate", value: null }, { note: "From an earlier successful generation" }]}
      />,
    );
    click(trigger(container));
    const receipt = query(container, ".bj-receipt");
    expect(rowsOf(receipt)).toEqual(["run a1b2c3d4", "From an earlier successful generation"]);
    expect(receipt.textContent).not.toMatch(/pdf|gate/);
  });

  it("keeps notes out of the definition list, in their original order", () => {
    installViewport(1280);
    const { container } = render(
      <TrailLine
        trail={TRAIL}
        receipt={[{ label: "run", value: "r1" }, { note: "Model not returned" }, { label: "pdf", value: "p1" }, { label: "gate", value: "g1" }, { note: "Second note" }]}
      />,
    );
    click(trigger(container));
    const receipt = query(container, ".bj-receipt");
    expect(rowsOf(receipt)).toEqual(["run r1", "Model not returned", "pdf p1", "gate g1", "Second note"]);
    expect(receipt.querySelectorAll("dl")).toHaveLength(2);
    for (const note of receipt.querySelectorAll(".bj-receipt-note")) expect(note.closest("dl")).toBeNull();
    // Every child of a <dl> is a row group holding a term and its description.
    for (const group of receipt.querySelectorAll("dl > *")) expect([...group.children].map((cell) => cell.tagName)).toEqual(["DT", "DD"]);
  });

  it("draws a receipt of only notes with no <dl> at all", () => {
    installViewport(1280);
    const { container } = render(<TrailLine trail={TRAIL} receipt={[{ note: "Only a note" }]} />);
    click(trigger(container));
    expect(container.querySelector("dl")).toBeNull();
    expect(rowsOf(query(container, ".bj-receipt"))).toEqual(["Only a note"]);
  });

  it("has no serious accessibility violation with a note in the receipt, inline or in the sheet", async () => {
    const receipt: ReceiptRowInput[] = [{ label: "run", value: "r1" }, { note: "Model not returned" }];
    const notesOnly: ReceiptRowInput[] = [{ note: "Only a note" }];
    installViewport(1280);
    for (const rows of [receipt, notesOnly]) {
      const inline = render(<TrailLine trail={TRAIL} receipt={rows} />);
      click(trigger(inline.container));
      await expectNoSeriousA11yViolations(inline.container);
      inline.unmount();
    }
    for (const rows of [receipt, notesOnly]) {
      const sheet = render(<TrailLine trail={TRAIL} receipt={rows} placement="sheet" />);
      click(trigger(sheet.container));
      await expectNoSeriousA11yViolations(query(document.body, ".bj-sheet-root"));
      sheet.unmount();
    }
  });

  it("is the same on the keyboard: a button, so Enter and Space click it, and it keeps focus", () => {
    installViewport(1280);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    trigger(container).focus();
    expect(document.activeElement).toBe(trigger(container));
    click(trigger(container));
    expect(document.activeElement).toBe(trigger(container));
    expect(query(container, ".bj-receipt").getAttribute("tabindex")).toBe("0");
  });

  it("can be controlled, so a margin can keep one receipt open at a time", () => {
    installViewport(1280);
    function Two() {
      const [openId, setOpenId] = useState<string | null>(null);
      return (
        <div>
          {["one", "two"].map((id) => (
            <TrailLine
              key={id}
              trail={{ actor: `Trail ${id}` }}
              receipt={[{ label: "id", value: id }]}
              open={openId === id}
              onOpenChange={(next) => setOpenId(next ? id : null)}
            />
          ))}
        </div>
      );
    }
    const { container } = render(<Two />);
    const [one, two] = [...container.querySelectorAll("button.bj-trail-trigger")];
    click(one);
    expect(container.querySelectorAll(".bj-receipt")).toHaveLength(1);
    click(two);
    expect(container.querySelectorAll(".bj-receipt")).toHaveLength(1);
    expect(one.getAttribute("aria-expanded")).toBe("false");
    expect(two.getAttribute("aria-expanded")).toBe("true");
  });

  it("has no serious accessibility violation, closed or open", async () => {
    installViewport(1280);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    await expectNoSeriousA11yViolations(container);
    click(trigger(container));
    await expectNoSeriousA11yViolations(container);
  });
});

describe("a trail line with a receipt (phones)", () => {
  it("opens the receipt as a bottom sheet, not inline", () => {
    installViewport(375);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} receiptTitle="Where this came from" />);
    expect(trigger(container).getAttribute("aria-haspopup")).toBe("dialog");
    click(trigger(container));
    expect(trigger(container).getAttribute("aria-expanded")).toBe("true");
    const sheet = query(document.body, '[role="dialog"]');
    expect(container.contains(sheet)).toBe(false);
    expect(query(sheet, "h2").textContent).toBe("Where this came from");
    expect(rowsOf(query(sheet, ".bj-receipt"))).toEqual(["run a1b2c3d4", "pdf 9f8e7d6c", "ATS 64 -> 72"]);
  });

  it("traps focus in the sheet, closes on Esc and returns focus to the line", () => {
    installViewport(375);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    click(trigger(container));
    const sheet = query(document.body, '[role="dialog"]');
    expect(document.activeElement).toBe(buttonNamed(sheet, "Close"));
    // Close, then the receipt well (scrollable, so it is a tab stop): Tab from the last wraps round.
    const well = query(sheet, ".bj-receipt");
    well.focus();
    keydown("Tab");
    expect(document.activeElement).toBe(buttonNamed(sheet, "Close"));
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(trigger(container).getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger(container));
  });

  it("closes from the Close button", () => {
    installViewport(375);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    click(trigger(container));
    click(buttonNamed(query(document.body, '[role="dialog"]'), "Close"));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(trigger(container));
  });

  it("switches between sheet and inline when the screen crosses 960px", () => {
    const viewport = installViewport(375);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    click(trigger(container));
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    viewport.setWidth(1280);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(container.querySelector(".bj-receipt")).not.toBeNull();
    expect(document.body.hasAttribute("style") && document.body.style.overflow === "hidden").toBe(false);
  });

  it("has no serious accessibility violation while the sheet is open", async () => {
    installViewport(375);
    const { container } = render(<TrailLine trail={TRAIL} receipt={RECEIPT} />);
    click(trigger(container));
    await expectNoSeriousA11yViolations(query(document.body, ".bj-sheet-root"));
  });

  it("can be forced to a sheet or inline whatever the screen", () => {
    installViewport(1280);
    const sheet = render(<TrailLine trail={TRAIL} receipt={RECEIPT} placement="sheet" />);
    click(trigger(sheet.container));
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    sheet.unmount();
    installViewport(375);
    const inline = render(<TrailLine trail={TRAIL} receipt={RECEIPT} placement="inline" />);
    click(trigger(inline.container));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(inline.container.querySelector(".bj-receipt")).not.toBeNull();
  });
});

describe("the static variant", () => {
  it("is plain text with nothing to open", () => {
    installViewport(1280);
    const { container } = render(<StaticTrailLine trail={TRAIL} />);
    expect(container.querySelector("button")).toBeNull();
    expect(words(query(container, "p.bj-trail"))).toBe("✦ Drafted by your model · on your OpenRouter key · Sep 22");
  });

  it("is what a line with no receipt, or with every receipt row omitted, becomes", () => {
    installViewport(1280);
    for (const receipt of [undefined, null, [], [{ label: "run", value: undefined }]] as const) {
      const { container, unmount } = render(<TrailLine trail={TRAIL} receipt={receipt} />);
      expect(container.querySelector("button")).toBeNull();
      expect(container.querySelector("p.bj-trail--static")).not.toBeNull();
      unmount();
    }
  });

  it("renders nothing when no words are left", () => {
    const { container } = render(<TrailLine trail={{ glyph: "model", actor: undefined, basis: null }} receipt={RECEIPT} />);
    expect(container.innerHTML).toBe("");
    const alone = render(<StaticTrailLine trail={{}} />);
    expect(alone.container.innerHTML).toBe("");
  });

  it("has no serious accessibility violation", async () => {
    const { container } = render(<StaticTrailLine trail={TRAIL} />);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("what a line looks like", () => {
  it("draws glyphs aria-hidden, never more than two colored ones, and the words carry the meaning", () => {
    installViewport(1280);
    const { container } = render(
      <TrailLine
        trail={{ glyph: "model", actor: "Searched", when: "Sep 22", also: { glyph: "failed", text: "2 searches failed" } }}
        receipt={RECEIPT}
      />,
    );
    const glyphs = [...container.querySelectorAll(".bj-trail-glyph")];
    expect(glyphs).toHaveLength(2);
    expect(glyphs.every((glyph) => glyph.getAttribute("aria-hidden") === "true")).toBe(true);
    expect(glyphs.map((glyph) => glyph.className)).toEqual(["bj-trail-glyph bj-trail-glyph--violet", "bj-trail-glyph bj-trail-glyph--danger"]);
    expect(words(trigger(container)).replace(/[✦✕]/g, "").replace(/\s+/g, " ")).toBe(" Searched · Sep 22 · 2 searches failed");
  });

  it("draws engine as the word, then the violet star", () => {
    const { container } = render(<StaticTrailLine trail={{ glyph: "engine", actor: "code-weighted from your model's sub-scores" }} />);
    expect(query(container, ".bj-trail-engine").textContent).toBe("engine");
    expect(query(container, ".bj-trail-glyph--violet").getAttribute("aria-hidden")).toBe("true");
    expect(words(container)).toBe("engine ✦ · code-weighted from your model's sub-scores");
  });

  it("draws dates and ids in mono", () => {
    const { container } = render(<StaticTrailLine trail={{ actor: ["Run", mono("a1b2c3d4")], when: "Sep 22" }} />);
    expect([...container.querySelectorAll(".bj-trail-mono")].map((element) => element.textContent)).toEqual(["a1b2c3d4", "Sep 22"]);
  });

  it("leaves a missing slot and its separator out of the page", () => {
    const { container } = render(<StaticTrailLine trail={{ glyph: "you", actor: "You tracked this", basis: undefined, when: "Sep 22" }} />);
    expect(words(container)).toBe("● You tracked this · Sep 22");
    expect(container.querySelectorAll(".bj-trail-sep")).toHaveLength(1);
  });

  it("never says a link was verified, nor names a model it was not told", () => {
    const { container } = render(
      <div>
        <StaticTrailLine trail={{ glyph: "passed", actor: "Link verified", basis: "on the company's job board at last poll" }} />
        <StaticTrailLine trail={{ glyph: "model", actor: "Drafted by an unknown model", when: "Sep 22" }} />
        <StaticTrailLine trail={{ glyph: "passed", actor: "Link checked" }} />
      </div>,
    );
    expect(findForbiddenWord(container.textContent ?? "")).toBeNull();
    // The slot that said it is left out, the rest of its line stays.
    expect([...container.querySelectorAll("p.bj-trail")].map(words)).toEqual([
      "\u2713 on the company's job board at last poll",
      "Sep 22",
      "\u2713 Link checked",
    ]);
  });
});
