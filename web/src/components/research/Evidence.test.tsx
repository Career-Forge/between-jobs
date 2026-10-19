// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { text } from "../../testing/forms";
import { query, render } from "../../testing/render";
import { contactConfidence, intelConfidence } from "../../lib/research";
import { Chip, CitationItem, ConfidenceMark, EvidenceItem, EvidenceList, EvidenceNote, SourceLink } from "./Evidence";

// The one evidence pattern the research sections share.

describe("a source line", () => {
  it("links to an http or https address in a new tab, with the destination named in words and no opener or referrer sent", () => {
    const { container } = render(<SourceLink url="https://example.com/a" title="A page" />);
    const link = query<HTMLAnchorElement>(container, "a");
    expect(link.getAttribute("href")).toBe("https://example.com/a");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(text(link)).toBe("A page (opens in a new tab)");
  });

  it("falls back to the address when there is no title", () => {
    const { container } = render(<SourceLink url="https://example.com/a" title={null} />);
    expect(text(query(container, "a"))).toContain("https://example.com/a");
  });

  it.each(["javascript:alert(1)", "data:text/html,<b>x</b>", "ftp://example.com/x", "//evil.example/x", "not an address", ""])("is not a link for %j", (url) => {
    const { container } = render(<SourceLink url={url} title="A title" />);
    expect(container.querySelector("a")).toBeNull();
    expect(text(container)).toBe("A title");
  });

  it("says so when there is neither a safe address nor a title", () => {
    const { container } = render(<SourceLink url="javascript:alert(1)" />);
    expect(text(container)).toBe("A source whose address is not shown");
  });
});

describe("a confidence mark", () => {
  it("fills the cells it is given and says the word, which a screen reader hears with its noun", () => {
    const { container } = render(<ConfidenceMark reading={intelConfidence("medium")!} />);
    expect(container.querySelectorAll(".bj-segbar-cell")).toHaveLength(3);
    expect(container.querySelectorAll(".bj-segbar-cell--filled")).toHaveLength(2);
    expect(query(container, ".bj-rs-conf-cells").getAttribute("aria-hidden")).toBe("true");
    expect(text(query(container, ".bj-rs-conf-word"))).toBe("Confidence: Medium");
  });

  it("reads a value it does not know as no mark at all", () => {
    expect(intelConfidence("certain")).toBeNull();
    expect(contactConfidence("constructor")).toBeNull();
    const { container } = render(
      <EvidenceList label="Sources">
        <EvidenceItem url="https://example.com/a" title="A" confidence={intelConfidence("certain")} />
      </EvidenceList>,
    );
    expect(container.querySelector(".bj-rs-conf")).toBeNull();
  });

  it("never reads the server's word 'verified' as a verification: a clear match is the model's reading", () => {
    expect(contactConfidence("verified")).toEqual({ cells: 4, of: 4, word: "Clear match" });
    expect(Object.values({ a: contactConfidence("verified"), b: contactConfidence("strong"), c: contactConfidence("inferred"), d: contactConfidence("unsupported") }).map((reading) => reading!.word.toLowerCase())).not.toContain("verified");
  });
});

describe("the rest of the pattern", () => {
  it("draws a citation as gold evidence with its label, a chip with its mark and word, and a note as a status", () => {
    const { container } = render(
      <>
        <EvidenceList label="Evidence">
          <CitationItem>Data pipelines</CitationItem>
        </EvidenceList>
        <Chip tone="dashed" glyph="evidence">
          Likely-staffed sponsor
        </Chip>
        <EvidenceNote>The server's reason.</EvidenceNote>
      </>,
    );
    expect(text(query(container, ".bj-rs-ev--citation"))).toBe("◆ Evidence Data pipelines");
    expect(text(query(container, ".bj-rs-chip--dashed"))).toBe("◆ Likely-staffed sponsor");
    expect(query(container, ".bj-rs-note").getAttribute("role")).toBe("status");
  });

  it("has no serious accessibility violation", async () => {
    const { container } = render(
      <EvidenceList label="Sources">
        <EvidenceItem url="https://example.com/a" title="A page" confidence={contactConfidence("strong")} />
        <CitationItem>Kafka</CitationItem>
      </EvidenceList>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});
